import socket
import threading
import sys
import time

active_peers = {}
peers_lock = threading.Lock()
shutdown_event = threading.Event()

my_listen_port = None
team_name = None

def get_local_ip():
    """
    Attempts to determine the local IP address used for outgoing connections.
    Useful for sharing with peers.
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

def handle_client(conn, addr):
    """
    Handles messages from a connected peer.
    If the peer sends a "CONNECT:<listening_port>" message, we update our record.
    Note: We update the global peer dictionary but do not change the local 'addr'
    used for printing messages so that the displayed port remains the original connection port.
    """
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

            if message.startswith("CONNECT:"):
                try:
                    sender_listen_port = int(message.split(":", 1)[1])
                    new_peer = (current_peer[0], sender_listen_port)
                except ValueError:
                    print(f"[ERROR] Invalid CONNECT message from {current_peer}")
                    continue

                with peers_lock:
                    if current_peer in active_peers:
                        active_peers.pop(current_peer)
                    if new_peer in active_peers:
                        try:
                            active_peers[new_peer].close()
                        except Exception:
                            pass
                    active_peers[new_peer] = conn
                print(f"[INFO] Updated connection info for peer {new_peer[0]}:{new_peer[1]}")
                continue

            if message.lower() == "exit":
                print(f"[INFO] {current_peer[0]}:{current_peer[1]} sent exit. Disconnecting.")
                break

            print(f"\n[Message from {current_peer[0]}:{current_peer[1]}]: {message}")

    except Exception as e:
        print(f"[ERROR] Exception with peer {current_peer[0]}:{current_peer[1]}: {e}")
    finally:
        with peers_lock:
            if current_peer in active_peers:
                active_peers.pop(current_peer)
        conn.close()

def server_thread(listen_socket):
    """
    Accepts incoming connections and spawns a dedicated thread for each client.
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

def query_active_peers():
    """
    Displays the list of active peers.
    """
    with peers_lock:
        if active_peers:
            print("\nConnected Peers:")
            for i, peer in enumerate(active_peers.keys(), 1):
                print(f" {i}. {peer[0]}:{peer[1]}")
        else:
            print("\nNo connected peers.")

def send_mandatory_messages():
    """
    Mandatorily sends a message to the two specified IP/port pairs.
    """
    mandatory_peers = [
        ("10.206.4.201", 1255),
        ("10.206.5.228", 6555)
    ]
    
    for ip, port in mandatory_peers:
        print(f"[INFO] Attempting to send mandatory message to {ip}:{port}")
        send_message(ip, port, f"{team_name}: Mandatory message: Hello from our peer!")

def main():
    global my_listen_port, team_name

    team_name = input("Enter your team name: ").strip()
    try:
        my_listen_port = int(input("Enter your port number (for incoming connections): ").strip())
    except ValueError:
        print("[ERROR] Invalid port number. Exiting.")
        sys.exit(1)

    local_ip = get_local_ip()
    print(f"[INFO] Your local IP address is: {local_ip}")
    print("[INFO] Share this IP and your port with peers for connecting externally.")

    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listen_socket.bind(("0.0.0.0", my_listen_port))
    except Exception as e:
        print(f"[ERROR] Could not bind to port {my_listen_port}: {e}")
        sys.exit(1)

    listen_socket.listen(5)
    print(f"Server listening on port {my_listen_port}...")

    server = threading.Thread(target=server_thread, args=(listen_socket,), daemon=True)
    server.start()

    time.sleep(2)
    send_mandatory_messages()

    while True:
        print("\n***** Menu *****")
        print("1. Send message")
        print("2. Query active peers")
        print("3. Connect to a peer")
        print("0. Quit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            target_ip = input("Enter the recipient's IP address: ").strip()
            try:
                target_port = int(input("Enter the recipient's port number: ").strip())
            except ValueError:
                print("[ERROR] Invalid port number.")
                continue
            message = input("Enter your message (type 'exit' to disconnect): ").strip()
            send_message(target_ip, target_port, f"{team_name}: {message}")

        elif choice == "2":
            query_active_peers()

        elif choice == "3":
            target_ip = input("Enter the peer's IP address to connect: ").strip()
            try:
                target_port = int(input("Enter the peer's port number: ").strip())
            except ValueError:
                print("[ERROR] Invalid port number.")
                continue
            connect_to_peer(target_ip, target_port)

        elif choice == "0":
            print("Exiting...")
            shutdown_event.set()
            break

        else:
            print("[ERROR] Invalid choice. Please try again.")

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
