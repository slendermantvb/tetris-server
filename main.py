import socket
import threading
import json
import time

players = []
leaderboard = {}

def handle_client(conn, addr):
    print("Jugador conectado:", addr)

    try:
        name = conn.recv(1024).decode()
        leaderboard.setdefault(name, 0)
        players.append(conn)

        while True:
            data = conn.recv(4096)
            if not data:
                break

            msg = json.loads(data.decode())

            # actualizar score
            if "score" in msg:
                leaderboard[name] = max(leaderboard[name], msg["score"])

            # reenviar a otros jugadores
            for p in players:
                if p != conn:
                    try:
                        p.send(data)
                    except:
                        pass

    except Exception as e:
        print("Error:", e)

    print("Jugador desconectado:", addr)
    if conn in players:
        players.remove(conn)
    conn.close()


def broadcast_leaderboard():
    while True:
        try:
            data = json.dumps({"leaderboard": leaderboard}).encode()
            for p in players:
                try:
                    p.send(data)
                except:
                    pass
            time.sleep(3)
        except:
            pass


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("0.0.0.0", 5555))
server.listen()

print("Servidor iniciado en puerto 5555...")

threading.Thread(target=broadcast_leaderboard, daemon=True).start()

while True:
    conn, addr = server.accept()
    threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
