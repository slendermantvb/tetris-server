import asyncio
import websockets
import json

players = set()
leaderboard = {}

async def handler(websocket):
    players.add(websocket)

    try:
        name = await websocket.recv()
        leaderboard.setdefault(name, 0)

        async for message in websocket:
            data = json.loads(message)

            if "score" in data:
                leaderboard[name] = max(leaderboard[name], data["score"])

            # enviar a todos
            for p in players:
                if p != websocket:
                    await p.send(message)

    except:
        pass

    players.remove(websocket)


async def broadcast():
    while True:
        if players:
            data = json.dumps({"leaderboard": leaderboard})
            await asyncio.gather(*[p.send(data) for p in players])
        await asyncio.sleep(3)


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8000):
        await broadcast()

asyncio.run(main())
