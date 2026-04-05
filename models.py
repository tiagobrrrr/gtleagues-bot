from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Match(db.Model):
    __tablename__ = "matches"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    match_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    kickoff = db.Column(db.String(64))
    week = db.Column(db.Integer)
    match_nr = db.Column(db.Integer)
    status = db.Column(db.Integer)
    season_id = db.Column(db.String(32), index=True)
    season_name = db.Column(db.String(128))
    tournament_name = db.Column(db.String(128))
    category_name = db.Column(db.String(128))
    sport_name = db.Column(db.String(64))
    channel = db.Column(db.String(32))

    home_player_id = db.Column(db.String(32), index=True)
    home_nickname = db.Column(db.String(64))
    home_team = db.Column(db.String(64))
    home_team_crest = db.Column(db.String(256))
    home_participant_id = db.Column(db.String(32))
    home_score = db.Column(db.Integer)

    away_player_id = db.Column(db.String(32), index=True)
    away_nickname = db.Column(db.String(64))
    away_team = db.Column(db.String(64))
    away_team_crest = db.Column(db.String(256))
    away_participant_id = db.Column(db.String(32))
    away_score = db.Column(db.Integer)


class Player(db.Model):
    __tablename__ = "players"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    player_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(64))


class PlayerStats(db.Model):
    __tablename__ = "player_stats"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    player_id = db.Column(db.String(32), index=True)
    season_id = db.Column(db.String(32), index=True)
    nickname = db.Column(db.String(64))
    team = db.Column(db.String(64))
    games_played = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    goals_for = db.Column(db.Integer, default=0)
    goals_against = db.Column(db.Integer, default=0)
    goals_diff = db.Column(db.Integer, default=0)
    win_rate = db.Column(db.Float, default=0.0)
    draw_rate = db.Column(db.Float, default=0.0)
    loss_rate = db.Column(db.Float, default=0.0)
    goals_for_per_match = db.Column(db.Float, default=0.0)
    goals_against_per_match = db.Column(db.Float, default=0.0)
    points_per_match = db.Column(db.Float, default=0.0)
    __table_args__ = (db.UniqueConstraint("player_id", "season_id"),)
