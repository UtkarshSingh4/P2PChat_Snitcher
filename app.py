import socket
import threading
import sys
import time
from flask import Flask, request, jsonify, render_template_string, redirect, url_for

###############################################
# Global variables and locks for peer networking
###############################################

# Active peers dictionary: key=(peer_ip, peer_port), value=socket object
active_peers = {}
peers_lock = threading.Lock()

# Event to signal shutdown.
shutdown_event = threading.Event()

# Global variables for our listening port and team name.
my_listen_port = None
team_name = None

# Chat history list to store messages (each is a dict with sender, message, timestamp)
chat_history = []
chat_lock = threading.Lock()

def add_chat_message(sender, message):
    """Thread-safe addition of a chat message."""
    with chat_lock:
        chat_history.append({
            'sender': sender,
            'message': message,
            'timestamp': time.strftime('%H:%M:%S')
        })

def get_local_ip():
    """
    Determines the local IP address used for outgoing connections.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
    return local_ip

###############################################
# Peer-to-peer socket functions
###############################################

def handle_client(conn, addr):
    """
    Handles messages from a connected peer.
    If the peer sends a "CONNECT:<listening_port>" message, we update our record.
    Also, non-control messages are added to the chat history.
    """
    global active_peers
    current_peer = addr
    try:
        while not shutdown_event.is_set():
            data = conn.recv(1024)
            if not data:
                print(f"[INFO] Connection closed by {current_peer[0]}:{current_peer[1]}")
                break
            message = data.decode().strip()
            if not message:
                continue

            # Handle a connection update message.
            if message.startswith("CONNECT:"):
                try:
                    sender_listen_port = int(message.split(":", 1)[1])
                    new_peer = (current_peer[0], sender_listen_port)
                except ValueError:
                    print(f"[ERROR] Invalid CONNECT message from {current_peer}")
                    continue

                with peers_lock:
                    if current_peer in active_peers:
                        active_peers.pop(current_peer, None)
                    if new_peer in active_peers:
                        try:
                            active_peers[new_peer].close()
                        except Exception:
                            pass
                    active_peers[new_peer] = conn
                current_peer = new_peer
                print(f"[INFO] Updated connection info for peer {new_peer[0]}:{new_peer[1]}")
                continue

            # If a peer sends "exit", then disconnect.
            if message.lower() == "exit":
                print(f"[INFO] {current_peer[0]}:{current_peer[1]} sent exit. Disconnecting.")
                break

            # Log the received message.
            print(f"[Message from {current_peer[0]}:{current_peer[1]}]: {message}")
            add_chat_message(f"{current_peer[0]}:{current_peer[1]}", message)

    except Exception as e:
        print(f"[ERROR] Exception with peer {current_peer[0]}:{current_peer[1]}: {e}")
    finally:
        with peers_lock:
            if current_peer in active_peers:
                active_peers.pop(current_peer, None)
        conn.close()

def server_thread(listen_socket):
    """
    Accept incoming connections and spawn a dedicated thread for each.
    """
    while not shutdown_event.is_set():
        try:
            listen_socket.settimeout(1.0)
            conn, addr = listen_socket.accept()
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_event.is_set():
                print(f"[ERROR] Accept failed: {e}")
            break

        print(f"[INFO] Accepted connection from {addr[0]}:{addr[1]}")
        with peers_lock:
            active_peers[addr] = conn
        client_thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        client_thread.start()

def send_message(target_ip, target_port, message):
    """
    Sends a message to the target peer.
    If no active connection exists, creates a new connection.
    """
    target = (target_ip, target_port)
    sock = None
    with peers_lock:
        sock = active_peers.get(target)

    if sock is None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)  # 10-second timeout for connecting.
            print(f"[DEBUG] Attempting to connect to {target_ip}:{target_port}")
            sock.connect(target)
            sock.settimeout(None)
            with peers_lock:
                active_peers[sock.getpeername()] = sock
            # Start a thread to handle incoming messages from this new connection
            client_thread = threading.Thread(target=handle_client, args=(sock, sock.getpeername()), daemon=True)
            client_thread.start()
        except Exception as e:
            print(f"[ERROR] Could not connect to {target_ip}:{target_port} - {e}")
            return

    try:
        sock.sendall(message.encode())
        print(f"[INFO] Message sent to {target_ip}:{target_port}")
        if message.lower() == "exit":
            with peers_lock:
                active_peers.pop(target, None)
            sock.close()
    except Exception as e:
        print(f"[ERROR] Failed to send message: {e}")
        with peers_lock:
            active_peers.pop(target, None)
        sock.close()

def connect_to_peer(target_ip, target_port):
    """
    Connects to a peer by sending a connection message that includes our listening port.
    """
    global my_listen_port
    target = (target_ip, target_port)
    sock = None
    with peers_lock:
        sock = active_peers.get(target)

    if sock is None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            print(f"[DEBUG] Attempting to connect to {target_ip}:{target_port}")
            sock.connect(target)
            sock.settimeout(None)
            with peers_lock:
                active_peers[sock.getpeername()] = sock
            client_thread = threading.Thread(target=handle_client, args=(sock, sock.getpeername()), daemon=True)
            client_thread.start()
        except Exception as e:
            print(f"[ERROR] Could not connect to {target_ip}:{target_port} - {e}")
            return

    connect_msg = f"CONNECT:{my_listen_port}"
    try:
        sock.sendall(connect_msg.encode())
        print(f"[INFO] Sent connection message to {target_ip}:{target_port}")
    except Exception as e:
        print(f"[ERROR] Failed to send connection message: {e}")
        with peers_lock:
            active_peers.pop(target, None)
        sock.close()

def send_mandatory_messages():
    """
    Sends a mandatory message to two specified IP/port pairs (optional).
    """
    mandatory_peers = [
        ("10.206.4.122", 1255),
        ("10.206.5.228", 6555)
    ]
    for ip, port in mandatory_peers:
        print(f"[INFO] Attempting to send mandatory message to {ip}:{port}")
        send_message(ip, port, "Mandatory message: Hello from our peer!")

###############################################
# Flask application and routes
###############################################

app = Flask(__name__)

# A helper to shut down the Flask server from a route
def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError("Not running with the Werkzeug Server")
    func()

html_template = """
<!DOCTYPE html>
<html>
<head>
  <title>Snitcher</title>
  <style>
    /* Brighter gradient background */
    body {
      margin: 0;
      padding: 0;
      font-family: Arial, sans-serif;
      background: linear-gradient(180deg, #6ec1ff, #72f5aa);
    }
    .header {
      position: relative;
      padding: 20px;
      font-size: 1.5rem;
      font-weight: bold;
      color: #0D47A1;
      text-shadow: 1px 1px 2px rgba(255, 255, 255, 0.4);
      background-color: rgba(255, 255, 255, 0.2);
      border-bottom: 1px solid rgba(255,255,255,0.3);
    }
    .header-title {
      text-align: center;
    }
    .end-program {
      position: absolute;
      top: 20px;
      right: 20px;
      background-color: #ff4444;
      border: none;
      color: #fff;
      padding: 8px 12px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.9rem;
    }
    .main-container {
      display: flex;
      flex-direction: row;
      margin: 20px;
      min-height: 80vh;
    }
    .peers-section {
      width: 300px;
      background-color: #f8f9fa;
      padding: 20px;
      box-sizing: border-box;
      border-radius: 12px;
      box-shadow: 0 4px 10px rgba(0,0,0,0.3);
      margin-right: 20px;
    }
    .peers-section h2 {
      margin-top: 0;
      color: #333;
    }
    .net-frequency {
      font-weight: bold;
      margin-bottom: 15px;
      color: #555;
    }
    .peers-section label {
      font-weight: 600;
      margin-bottom: 5px;
      display: block;
      color: #333;
    }
    .peers-section input {
      width: 100%;
      margin-bottom: 10px;
      padding: 8px;
      box-sizing: border-box;
    }
    .peers-section button {
      width: 100%;
      padding: 8px;
      margin-bottom: 20px;
      cursor: pointer;
      background-color: #007bff;
      color: #fff;
      border: none;
      border-radius: 4px;
    }
    .peer-list {
      margin-top: 20px;
      max-height: 300px; /* Added max-height for scrollable area */
      overflow-y: auto;  /* Enables vertical scrolling */
    }
    .peer-list::-webkit-scrollbar {
      width: 8px;
    }
    .peer-list::-webkit-scrollbar-thumb {
      background-color: #ccc;
      border-radius: 4px;
    }
    .peer-list .peer-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background-color: #ffffff;
      border: 1px solid #ddd;
      padding: 5px 10px;
      margin-bottom: 5px;
      border-radius: 4px;
    }
    .peer-list .peer-item .msg-btn {
      width: auto;
      padding: 4px 8px;
      margin-bottom: 0;
      background-color: #28a745;
      border: none;
      border-radius: 4px;
      color: #fff;
      cursor: pointer;
      font-size: 1rem;
    }
    .chat-section {
      flex: 1;
      display: flex;
      flex-direction: column;
      padding: 20px;
      box-sizing: border-box;
      border-radius: 12px;
      
      overflow-y: auto;
      overflow-x: auto;        /* Horizontal scroll if content is too wide */
      white-space: nowrap;     /* Prevent wrapping so horizontal scrolling occurs */
      box-shadow: 0 4px 10px rgba(0,0,0,0.3);
      background-color: #f8f9fa;
    }
    .chat-section h2 {
      margin-top: 0;
      color: #333;
    }
    .chat-controls {
      display: flex;
      margin-bottom: 10px;
      gap: 10px;
    }
    .chat-controls input[type="text"], .chat-controls input[type="number"] {
      background-color: #ccefff;
      color: #000;
      border: 1px solid #007bff;
      border-radius: 4px;
      padding: 8px;
      width: 120px;
    }
    .chat-window {
      flex: 1;
      background-color: #ffffff;
      border: 1px solid #ddd;
      padding: 10px;
      border-radius: 12px;
      height: 50%
      weight: 50%
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
      overflow-y: scroll;
      overflow-x: auto;
      margin-bottom: 10px;
    }
    .chat-window::-webkit-scrollbar {
      width: 8px;
    }
    .chat-window::-webkit-scrollbar-thumb {
      background-color: #ccc;
      border-radius: 4px;
    }
    .chat-input {
      display: flex;
      align-items: center;
    }
    .chat-input input[type="text"] {
      flex: 1;
      padding: 8px;
      margin-right: 10px;
      box-sizing: border-box;
    }
    .chat-input button {
      width: 100px;
      padding: 8px;
      background-color: #007bff;
      color: #fff;
      border: none;
      border-radius: 4px;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="header-title">Snicher_Chat</div>
    <form action="{{ url_for('quit_app') }}" method="POST">
      <button type="submit" class="end-program">End Program</button>
    </form>
  </div>
  <div class="main-container">
    <!-- Left: Peer Connection Section -->
    <div class="peers-section">
      <h2>Peer Connection</h2>
      <div class="net-frequency">Net Frequency: 3s</div>
      <!-- Connect to Peer Form -->
      <form id="connectPeerForm" method="POST" action="{{ url_for('connect') }}">
        <label>Peer IP</label>
        <input type="text" name="peer_ip" placeholder="Peer IP" required />
        <label>Peer Port</label>
        <input type="number" name="peer_port" placeholder="Peer Port" required />
        <button type="submit">Connect Peer</button>
      </form>

      <!-- Active Peers -->
      <h2>Active Peers</h2>
      <div class="peer-list" id="peersList">
        <!-- Active peers appended via JavaScript -->
      </div>
    </div>

    <!-- Right: Chat Section -->
    <div class="chat-section">
      <h2>Chat</h2>
      <!-- Instead of a dropdown, we have two inputs for IP and Port -->
      <div class="chat-controls">
        <input type="text" id="chatIp" name="target_ip" placeholder="Peer IP" required />
        <input type="number" id="chatPort" name="target_port" placeholder="Peer Port" required />
      </div>
      <div class="chat-window" id="chatHistory">
        <!-- Chat messages will be inserted here -->
      </div>
      <form id="sendForm" class="chat-input" method="POST" action="{{ url_for('send') }}">
        <input type="text" name="message" placeholder="Type your message..." required />
        <!-- We'll store IP and Port in hidden fields before submit -->
        <input type="hidden" name="target_ip" id="hiddenIp" />
        <input type="hidden" name="target_port" id="hiddenPort" />
        <button type="submit">Send</button>
      </form>
    </div>
  </div>
  
  <script>
    // On form submission, copy the visible IP/Port into hidden fields
    const sendForm = document.getElementById('sendForm');
    const chatIp = document.getElementById('chatIp');
    const chatPort = document.getElementById('chatPort');
    const hiddenIp = document.getElementById('hiddenIp');
    const hiddenPort = document.getElementById('hiddenPort');

    sendForm.addEventListener('submit', function() {
      hiddenIp.value = chatIp.value.trim();
      hiddenPort.value = chatPort.value.trim();
    });

    // Fill the chat IP/Port fields when user clicks message icon
    function fillChatFields(peer) {
      const [ip, port] = peer.split(":");
      chatIp.value = ip;
      chatPort.value = port;
    }

    // Function to fetch updates (chat history and active peers)
    function fetchUpdates() {
      fetch('{{ url_for("updates") }}')
        .then(response => response.json())
        .then(data => {
          // Update chat history
          const chatDiv = document.getElementById("chatHistory");
          chatDiv.innerHTML = "";
          data.chat_history.forEach(msg => {
            const p = document.createElement("p");
            p.innerHTML = "<strong>[" + msg.timestamp + "] " + msg.sender + ":</strong> " + msg.message;
            chatDiv.appendChild(p);
          });
          chatDiv.scrollTop = chatDiv.scrollHeight;
          
          // Update active peers list
          const peersList = document.getElementById("peersList");
          peersList.innerHTML = "";
          
          data.active_peers.forEach(peer => {
            // Add to peer list with a message icon
            const div = document.createElement("div");
            div.className = "peer-item";
            div.innerHTML = `
              <span>${peer}</span>
              <button type="button" class="msg-btn" onclick="fillChatFields('${peer}')">💬</button>
            `;
            peersList.appendChild(div);
          });
        })
        .catch(err => console.error("Error fetching updates:", err));
    }
    
    // Poll updates every 3 seconds.
    setInterval(fetchUpdates, 3000);
    // Also fetch once when the page loads.
    fetchUpdates();
  </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(html_template)

@app.route("/quit", methods=["POST"])
def quit_app():
    """
    Route to shut down the Flask server gracefully.
    """
    shutdown_server()
    return "Shutting down..."

@app.route("/connect", methods=["POST"])
def connect():
    """
    Connect to a peer by IP/port.
    """
    peer_ip = request.form.get("peer_ip", "").strip()
    try:
        peer_port = int(request.form.get("peer_port", "").strip())
    except ValueError:
        return redirect(url_for('index'))
    
    threading.Thread(target=connect_to_peer, args=(peer_ip, peer_port), daemon=True).start()
    return redirect(url_for('index'))

@app.route("/send", methods=["POST"])
def send():
    """
    Send a chat message to the specified IP and port.
    """
    ip = request.form.get("target_ip", "").strip()
    port_str = request.form.get("target_port", "").strip()
    message = request.form.get("message", "").strip()
    if not ip or not port_str or not message:
        return redirect(url_for('index'))

    try:
        port = int(port_str)
    except ValueError:
        return redirect(url_for('index'))

    # Add local message to chat history.
    add_chat_message(team_name, message)
    # Send message to target peer
    send_message(ip, port, f"{team_name}: {message}")
    return redirect(url_for('index'))

@app.route("/updates", methods=["GET"])
def updates():
    """
    Return JSON with current chat history and active peers.
    """
    with chat_lock:
        chat = list(chat_history)
    with peers_lock:
        connected = [f"{ip}:{port}" for (ip, port) in active_peers.keys()]
    return jsonify({
        "chat_history": chat,
        "active_peers": connected
    })

def main():
    global my_listen_port, team_name

    # Get team name and listening port from the user.
    team_name = input("Enter your team name: ").strip()
    try:
        my_listen_port = int(input("Enter your port number (for incoming peer connections): ").strip())
    except ValueError:
        print("[ERROR] Invalid port number. Exiting.")
        sys.exit(1)

    local_ip = get_local_ip()
    print(f"[INFO] Your local IP address is: {local_ip}")
    print("[INFO] Share this IP and your port with peers for connecting externally.")

    # Set up the listening socket for peer connections.
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listen_socket.bind(("0.0.0.0", my_listen_port))
    except Exception as e:
        print(f"[ERROR] Could not bind to port {my_listen_port}: {e}")
        sys.exit(1)

    listen_socket.listen(5)
    print(f"[INFO] Peer server listening on port {my_listen_port}...")

    # Start the peer server thread.
    server = threading.Thread(target=server_thread, args=(listen_socket,), daemon=True)
    server.start()

    # Give the server thread a moment to start.
    time.sleep(2)

    # Send mandatory messages (optional).
    send_mandatory_messages()

    # Now start the Flask web interface (running on port 5000).
    try:
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        shutdown_event.set()
        with peers_lock:
            for peer, sock in list(active_peers.items()):
                try:
                    sock.close()
                except Exception:
                    pass
            active_peers.clear()
        try:
            listen_socket.close()
        except Exception:
            pass
        server.join(timeout=2)
        print("Goodbye!")

if __name__ == "__main__":
    main()
