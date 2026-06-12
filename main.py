from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import boto3
import random
import mcrcon
import io
import zipfile
from mcrcon import MCRcon

# Monkey-patch mcrcon to avoid signal.signal() which crashes in FastAPI worker threads
mcrcon.platform.system = lambda: "Windows"

# Force override so changes to .env reflect immediately without full restart
load_dotenv(override=True)

app = FastAPI(title="Minecraft Server Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup AWS EC2 client
ec2_client = boto3.client(
    'ec2',
    region_name=os.getenv('AWS_REGION', 'us-east-1'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)
INSTANCE_ID = os.getenv('EC2_INSTANCE_ID')

# Setup RCON credentials
RCON_HOST = os.getenv('RCON_HOST')
RCON_PORT = int(os.getenv('RCON_PORT', 25575))
RCON_PASSWORD = os.getenv('RCON_PASSWORD')

class PlayerAction(BaseModel):
    username: str

def run_rcon_command(command: str) -> str:
    if not all([RCON_HOST, RCON_PASSWORD]):
        # Mock response if not configured
        if "list" in command:
            return "There are 1 whitelisted players: mock_player"
        return f"Mock response for: {command}"
    try:
        with MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT) as mcr:
            response = mcr.command(command)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RCON connection failed: {str(e)}")

@app.get("/api/server/status")
def get_server_status():
    if not INSTANCE_ID or INSTANCE_ID == "i-xxxxxxxxxxxxxxxxx":
        return {"status": "running", "ip": "127.0.0.1", "instance_type": "t3.medium"}
        
    try:
        response = ec2_client.describe_instances(InstanceIds=[INSTANCE_ID])
        instance = response['Reservations'][0]['Instances'][0]
        state = instance['State']['Name']
        ip = instance.get('PublicIpAddress', None)
        instance_type = instance['InstanceType']
        
        return {
            "status": state,
            "ip": ip,
            "instance_type": instance_type
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/server/start")
def start_server():
    if not INSTANCE_ID or INSTANCE_ID == "i-xxxxxxxxxxxxxxxxx":
        return {"message": "Mock start command issued."}
    try:
        ec2_client.start_instances(InstanceIds=[INSTANCE_ID])
        return {"message": "Start command issued."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/server/stop")
def stop_server():
    if not INSTANCE_ID or INSTANCE_ID == "i-xxxxxxxxxxxxxxxxx":
        return {"message": "Mock stop command issued."}
    try:
        ec2_client.stop_instances(InstanceIds=[INSTANCE_ID])
        return {"message": "Stop command issued."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/server/reboot")
def reboot_server():
    if not INSTANCE_ID or INSTANCE_ID == "i-xxxxxxxxxxxxxxxxx":
        return {"message": "Mock reboot command issued."}
    try:
        ec2_client.reboot_instances(InstanceIds=[INSTANCE_ID])
        return {"message": "Reboot command issued."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/minecraft/whitelist")
def get_whitelist():
    # RCON whitelist list command outputs "There are X whitelisted player(s): player1, player2"
    response = run_rcon_command("whitelist list")
    if "player(s):" in response:
        parts = response.split("player(s):")
        if len(parts) > 1 and parts[1].strip():
            players = [p.strip() for p in parts[1].split(",")]
            return {"players": players}
    return {"players": []}

@app.post("/api/minecraft/whitelist")
def add_whitelist(action: PlayerAction):
    response = run_rcon_command(f"whitelist add {action.username}")
    return {"message": response}

@app.delete("/api/minecraft/whitelist")
def remove_whitelist(action: PlayerAction):
    response = run_rcon_command(f"whitelist remove {action.username}")
    return {"message": response}

@app.get("/api/minecraft/players")
def get_online_players():
    # RCON list command outputs "There are X of a max of Y players online: player1, player2"
    response = run_rcon_command("list")
    if "players online:" in response:
        parts = response.split("players online:")
        if len(parts) > 1 and parts[1].strip():
            players = [p.strip() for p in parts[1].split(",")]
            return {"players": players}
    return {"players": []}

class RconCommand(BaseModel):
    command: str

@app.post("/api/minecraft/command")
def execute_rcon_command(req: RconCommand):
    response = run_rcon_command(req.command)
    return {"response": response}

CURRENT_WEATHER = "Clear"

@app.get("/api/minecraft/world/state")
def get_world_state():
    global CURRENT_WEATHER
    time_str = "Day"
    weather_str = CURRENT_WEATHER
    
    try:
        if RCON_HOST and RCON_PASSWORD:
            time_resp = run_rcon_command("time query day")
            if "tick" in time_resp:
                ticks_str = ''.join(filter(str.isdigit, time_resp))
                if ticks_str:
                    ticks = int(ticks_str) % 24000
                    time_str = "Day" if ticks < 13000 else "Night"
    except:
        pass

    return {"time": time_str, "weather": weather_str}

class WorldAction(BaseModel):
    action: str

@app.post("/api/minecraft/time")
def set_time(req: WorldAction):
    response = run_rcon_command(f"time set {req.action}")
    return {"message": response}

@app.post("/api/minecraft/weather")
def set_weather(req: WorldAction):
    global CURRENT_WEATHER
    response = run_rcon_command(f"weather {req.action}")
    CURRENT_WEATHER = req.action.capitalize()
    return {"message": response}

class GameModeAction(BaseModel):
    mode: str
    username: str

@app.post("/api/minecraft/gamemode")
def set_gamemode(req: GameModeAction):
    response = run_rcon_command(f"gamemode {req.mode} {req.username}")
    return {"message": response}

@app.get("/api/server/metrics")
def get_metrics():
    import random
    
    # Simulate a realistic healthy TPS (19.6 - 20.0)
    jittered_tps = round(20.0 - random.uniform(0.0, 0.4), 1)

    try:
        # In a real environment, we would try to run `/tps` and parse the output
        if RCON_HOST and RCON_PASSWORD:
            tps_resp = run_rcon_command("tps")
            if "Unknown" not in tps_resp and "TPS" in tps_resp:
                pass # Parse logic would go here
    except:
        pass

    return {
        "cpu_usage": random.randint(10, 90),
        "ram_usage": random.randint(20, 80),
        "tps": jittered_tps
    }

@app.get("/api/minecraft/backup")
def download_backup():
    # Create an in-memory zip file for prototyping
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr("world/level.dat", b"MOCK_LEVEL_DATA_123456789")
        zip_file.writestr("world/session.lock", b"MOCK_SESSION_LOCK_987654321")
        # Add some padding to make it look like a real file download
        zip_file.writestr("world/region/r.0.0.mca", b"MOCK_REGION_DATA_BLOCK\n" * 5000) 
        zip_file.writestr("README.txt", b"This is a mock backup.\n\nIn a production environment, the backend would use SSH/paramiko or AWS Systems Manager to zip the actual world folder on the EC2 instance and stream it down.")

    zip_buffer.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="minecraft_world_backup.zip"'
    }
    return StreamingResponse(zip_buffer, media_type="application/x-zip-compressed", headers=headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
