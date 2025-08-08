# redis_store.py
import redis
import json

from package.utils import safe_call, format_log


class RedisStore(object):
    def __init__(self, host='localhost', port=6379, db=0):
        self.r = redis.StrictRedis(host=host,
                                   port=port,
                                   db=db)

    @staticmethod
    def _player_key(player_id):
        return "player:%s" % player_id

    @staticmethod
    def _game_key(game_session_id):
        return "game:%s" % game_session_id

    @safe_call
    def save_player_state(self, player_id, state_dict):
        key = RedisStore._player_key(player_id)
        self.r.set(key, json.dumps(state_dict))

    @safe_call
    def read_player_state(self, player_id):
        key = RedisStore._player_key(player_id)
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None
    
    @safe_call
    def restore_player_state(self, game_session_id, player_id):
        data = self.read_game_state(game_session_id)
        if data:
            for p in data["players"]:
                if p["name"] == player_id:
                    return p
        return None

    @safe_call
    def save_player_game(self, player_id, game_session_id):
        key = RedisStore._player_key(player_id)
        self.r.set(key+":game", game_session_id)

    @safe_call
    def read_player_game(self, player_id):
        key = RedisStore._player_key(player_id)
        return self.r.get(key+":game")

    @safe_call
    def delete_player_game(self, player_id):
        key = RedisStore._player_key(player_id)
        self.r.delete(key+":game")

    @safe_call
    def delete_player_state(self, player_id):
        key = RedisStore._player_key(player_id)
        self.r.delete(key)

    @safe_call
    def save_game_state(self, game_session_id, game_state_dict):
        key = self._game_key(game_session_id)
        self.r.set(key, json.dumps(game_state_dict))

    @safe_call
    def read_game_state(self, game_session_id):
        key = self._game_key(game_session_id)
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    @safe_call
    def delete_game_state(self, game_session_id):
        key = self._game_key(game_session_id)
        game_data = self.read_game_state(game_session_id)
        if game_data is None:
            print(format_log("Game state not found when deleting."))
            return

        for p in game_data["players"]:
            self.delete_player_game(p["name"])
        self.r.delete(key)
