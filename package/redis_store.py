# redis_store.py
import redis
import json

class RedisStore(object):
    def __init__(self, host='localhost', port=6379, db=0):
        self.r = redis.StrictRedis(host=host, port=port, db=db)

    def _make_key(self, room_id, player_id):
        return "room:%s:player:%s" % (room_id, player_id)

    def save_player_state(self, room_id, player_id, state):
        key = self._make_key(room_id, player_id)
        self.r.set(key, json.dumps(state))

    def get_player_state(self, room_id, player_id):
        key = self._make_key(room_id, player_id)
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    def delete_player_state(self, room_id, player_id):
        key = self._make_key(room_id, player_id)
        self.r.delete(key)

    def get_all_players_in_room(self, room_id):
        pattern = "room:%s:player:*" % room_id
        keys = self.r.keys(pattern)
        players = {}
        for key in keys:
            player_id = key.decode('utf-8').split(":")[-1]
            data = self.r.get(key)
            if data:
                players[player_id] = json.loads(data)
        return players
