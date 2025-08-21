import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
from models import db, Player, Score, Achievement, PlayerAchievement, Tournament, TournamentParticipation, GameRoom
from config import config

# Inicialización de la app
app = Flask(__name__)
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[env])

# Inicialización de extensiones
db.init_app(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)
CORS(app, resources={r"/*": {"origins": app.config['CORS_ORIGINS']}})
socketio = SocketIO(app, cors_allowed_origins=app.config['CORS_ORIGINS'], async_mode='eventlet')

# Diccionario para salas activas
active_rooms = {}

# Rutas de autenticación
@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    if not data.get('username') or not data.get('password') or not data.get('email'):
        return jsonify({'error': 'Missing required fields'}), 400
    
    if Player.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    if Player.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 400
    
    player = Player(
        username=data['username'],
        email=data['email']
    )
    player.set_password(data['password'])
    
    db.session.add(player)
    db.session.commit()
    
    # Crear logros iniciales
    create_initial_achievements()
    
    return jsonify({
        'message': 'Player registered successfully',
        'player_id': player.id,
        'token': player.get_token()
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    if not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Missing username or password'}), 400
    
    player = Player.query.filter_by(username=data['username']).first()
    
    if not player or not player.check_password(data['password']):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    return jsonify({
        'message': 'Login successful',
        'player_id': player.id,
        'token': player.get_token()
    }), 200

# Rutas de puntuaciones
@app.route('/api/score', methods=['POST'])
@jwt_required()
def submit_score():
    player_id = get_jwt_identity()
    data = request.get_json()
    
    score = Score(
        player_id=player_id,
        score=data.get('score', 0),
        level=data.get('level', 1),
        lines_cleared=data.get('lines_cleared', 0),
        game_mode=data.get('game_mode', 'classic'),
        duration=data.get('duration', 0),
        replay_data=data.get('replay_data')
    )
    
    db.session.add(score)
    db.session.commit()
    
    # Verificar logros
    check_achievements(player_id, score)
    
    return jsonify({'message': 'Score saved successfully'}), 201

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    limit = request.args.get('limit', 10, type=int)
    game_mode = request.args.get('game_mode', 'classic')
    timeframe = request.args.get('timeframe', 'all')
    
    query = Score.query.filter_by(game_mode=game_mode)
    
    # Filtrar por timeframe
    if timeframe == 'day':
        query = query.filter(Score.timestamp >= datetime.utcnow() - timedelta(days=1))
    elif timeframe == 'week':
        query = query.filter(Score.timestamp >= datetime.utcnow() - timedelta(weeks=1))
    
    scores = query.order_by(Score.score.desc()).limit(limit).all()
    
    return jsonify([score.to_dict() for score in scores])

# Rutas de perfil
@app.route('/api/player/profile', methods=['GET'])
@jwt_required()
def get_player_profile():
    player_id = get_jwt_identity()
    player = Player.query.get(player_id)
    
    if not player:
        return jsonify({'error': 'Player not found'}), 404
    
    # Calcular estadísticas
    total_games = Score.query.filter_by(player_id=player_id).count()
    total_score = db.session.query(db.func.sum(Score.score)).filter_by(player_id=player_id).scalar() or 0
    high_score = db.session.query(db.func.max(Score.score)).filter_by(player_id=player_id).scalar() or 0
    total_lines = db.session.query(db.func.sum(Score.lines_cleared)).filter_by(player_id=player_id).scalar() or 0
    total_duration = db.session.query(db.func.sum(Score.duration)).filter_by(player_id=player_id).scalar() or 0
    
    # Calcular win rate (para modo battle)
    battle_games = Score.query.filter_by(player_id=player_id, game_mode='battle').count()
    battle_wins = Score.query.filter_by(player_id=player_id, game_mode='battle').filter(Score.score > 1000).count()
    win_rate = (battle_wins / battle_games * 100) if battle_games > 0 else 0
    
    return jsonify({
        'player': player.to_dict(),
        'stats': {
            'games_played': total_games,
            'total_score': total_score,
            'high_score': high_score,
            'total_lines': total_lines,
            'play_time': total_duration // 3600,  # Convertir a horas
            'win_rate': round(win_rate, 2)
        }
    })

@app.route('/api/player/achievements', methods=['GET'])
@jwt_required()
def get_player_achievements():
    player_id = get_jwt_identity()
    
    # Obtener logros del jugador
    player_achievements = PlayerAchievement.query.filter_by(player_id=player_id).all()
    unlocked_ids = [pa.achievement_id for pa in player_achievements]
    
    # Obtener todos los logros disponibles
    all_achievements = Achievement.query.all()
    
    result = []
    for achievement in all_achievements:
        if achievement.id in unlocked_ids:
            # Logro desbloqueado
            pa = next(pa for pa in player_achievements if pa.achievement_id == achievement.id)
            result.append(pa.to_dict())
        else:
            # Logro no desbloqueado
            result.append({
                'id': achievement.id,
                'name': achievement.name,
                'description': achievement.description,
                'icon': achievement.icon,
                'unlocked': False
            })
    
    return jsonify(result)

# Rutas de torneos
@app.route('/api/tournaments', methods=['GET'])
def get_tournaments():
    tournaments = Tournament.query.filter(Tournament.start_time >= datetime.utcnow()).all()
    return jsonify([t.to_dict() for t in tournaments])

@app.route('/api/tournaments/<int:tournament_id>/join', methods=['POST'])
@jwt_required()
def join_tournament(tournament_id):
    player_id = get_jwt_identity()
    
    tournament = Tournament.query.get(tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament is not accepting new participants'}), 400
    
    # Verificar si ya está inscrito
    existing = TournamentParticipation.query.filter_by(
        tournament_id=tournament_id,
        player_id=player_id
    ).first()
    
    if existing:
        return jsonify({'error': 'Already registered for this tournament'}), 400
    
    # Verificar cupo
    current_count = TournamentParticipation.query.filter_by(tournament_id=tournament_id).count()
    if current_count >= tournament.max_players:
        return jsonify({'error': 'Tournament is full'}), 400
    
    participation = TournamentParticipation(
        tournament_id=tournament_id,
        player_id=player_id
    )
    
    db.session.add(participation)
    db.session.commit()
    
    return jsonify({'message': 'Successfully joined tournament'}), 200

# Rutas de salas
@app.route('/api/rooms', methods=['POST'])
@jwt_required()
def create_room():
    player_id = get_jwt_identity()
    data = request.get_json()
    
    room = GameRoom(
        name=data.get('name', f'Room {datetime.now().strftime("%H:%M")}'),
        game_mode=data.get('game_mode', 'classic'),
        max_players=data.get('max_players', 4),
        host_id=player_id
    )
    
    db.session.add(room)
    db.session.commit()
    
    # Inicializar sala en memoria
    active_rooms[room.id] = {
        'players': [player_id],
        'game_state': None
    }
    
    return jsonify({
        'message': 'Room created successfully',
        'room': room.to_dict()
    }), 201

@app.route('/api/rooms/<int:room_id>/join', methods=['POST'])
@jwt_required()
def join_room(room_id):
    player_id = get_jwt_identity()
    
    room = GameRoom.query.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found'}), 404
    
    if room.status != 'waiting':
        return jsonify({'error': 'Room is not accepting new players'}), 400
    
    # Verificar si hay espacio
    if room_id in active_rooms:
        if len(active_rooms[room_id]['players']) >= room.max_players:
            return jsonify({'error': 'Room is full'}), 400
        
        if player_id not in active_rooms[room_id]['players']:
            active_rooms[room_id]['players'].append(player_id)
    
    return jsonify({
        'message': 'Joined room successfully',
        'room': room.to_dict()
    }), 200

# Eventos WebSocket
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('auth')
def handle_auth(data):
    token = data.get('token')
    if not token:
        return
    
    try:
        # Verificar token (simplificado para el ejemplo)
        player_id = 1  # En producción, decodificar el JWT
        join_room(f'player_{player_id}')
        emit('auth_success', {'player_id': player_id})
    except:
        emit('auth_error', {'message': 'Invalid token'})

@socketio.on('join_room')
def handle_join_room(data):
    room_id = data.get('room_id')
    if room_id in active_rooms:
        join_room(f'room_{room_id}')
        emit('room_joined', {
            'room_id': room_id,
            'players': active_rooms[room_id]['players']
        })

@socketio.on('chat')
def handle_chat(data):
    room_id = data.get('room_id')
    message = data.get('message')
    player_id = data.get('player_id')
    
    if room_id and message and player_id:
        player = Player.query.get(player_id)
        if player:
            emit('chat', {
                'username': player.username,
                'message': message,
                'timestamp': datetime.utcnow().isoformat()
            }, room=f'room_{room_id}')

@socketio.on('game_update')
def handle_game_update(data):
    room_id = data.get('room_id')
    game_state = data.get('game_state')
    
    if room_id in active_rooms:
        active_rooms[room_id]['game_state'] = game_state
        emit('game_update', game_state, room=f'room_{room_id}')

# Funciones auxiliares
def create_initial_achievements():
    """Crear logros iniciales si no existen"""
    if Achievement.query.count() == 0:
        achievements = [
            Achievement(name='First Steps', description='Complete your first game', icon='star'),
            Achievement(name='Line Master', description='Clear 100 lines total', icon='lines'),
            Achievement(name='Score Hunter', description='Reach 10,000 points', icon='score'),
            Achievement(name='Speed Demon', description='Clear 40 lines in under 5 minutes', icon='speed'),
            Achievement(name='Marathon Runner', description='Clear 200 lines in one game', icon='marathon'),
            Achievement(name='Tetris Master', description='Get 10 Tetris clears (4 lines)', icon='tetris'),
            Achievement(name='Combo King', description='Get a 5x combo', icon='combo'),
            Achievement(name='Veteran', description='Play 100 games', icon='veteran')
        ]
        
        for achievement in achievements:
            db.session.add(achievement)
        
        db.session.commit()

def check_achievements(player_id, score):
    """Verificar y desbloquear logros"""
    player = Player.query.get(player_id)
    if not player:
        return
    
    # Logro: Primer juego
    first_game = Achievement.query.filter_by(name='First Steps').first()
    if first_game:
        has_achievement = PlayerAchievement.query.filter_by(
            player_id=player_id,
            achievement_id=first_game.id
        ).first()
        
        if not has_achievement:
            pa = PlayerAchievement(player_id=player_id, achievement_id=first_game.id)
            db.session.add(pa)
            
            # Notificar via WebSocket
            socketio.emit('achievement', {
                'player_id': player_id,
                'achievement': first_game.to_dict()
            }, room=f'player_{player_id}')
    
    # Logro: 10,000 puntos
    if score.score >= 10000:
        score_hunter = Achievement.query.filter_by(name='Score Hunter').first()
        if score_hunter:
            has_achievement = PlayerAchievement.query.filter_by(
                player_id=player_id,
                achievement_id=score_hunter.id
            ).first()
            
            if not has_achievement:
                pa = PlayerAchievement(player_id=player_id, achievement_id=score_hunter.id)
                db.session.add(pa)
                
                socketio.emit('achievement', {
                    'player_id': player_id,
                    'achievement': score_hunter.to_dict()
                }, room=f'player_{player_id}')
    
    # Logro: 100 líneas totales
    total_lines = db.session.query(db.func.sum(Score.lines_cleared)).filter_by(player_id=player_id).scalar() or 0
    if total_lines >= 100:
        lines_master = Achievement.query.filter_by(name='Line Master').first()
        if lines_master:
            has_achievement = PlayerAchievement.query.filter_by(
                player_id=player_id,
                achievement_id=lines_master.id
            ).first()
            
            if not has_achievement:
                pa = PlayerAchievement(player_id=player_id, achievement_id=lines_master.id)
                db.session.add(pa)
                
                socketio.emit('achievement', {
                    'player_id': player_id,
                    'achievement': lines_master.to_dict()
                }, room=f'player_{player_id}')
    
    db.session.commit()

# Comando para inicializar la base de datos
@app.cli.command()
def init_db():
    """Initialize the database"""
    db.create_all()
    create_initial_achievements()
    print('Database initialized!')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_initial_achievements()
    
    if env == 'production':
        socketio.run(app, host='0.0.0.0', port=10000)
    else:
        socketio.run(app, debug=True)
