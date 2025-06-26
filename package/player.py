import random

class Player(object):
    def __init__(self, name):
        self.name = name
        self.answer = random.sample(list('0123456789'), 4)
        self.number_hand = []   # players draw number cards
        self.tool_hand = []     # players draw tool cards
        self.best_A = 0
        self.best_B = 0
        self.guess_histories = []

        self.cmd_queue = None
        self.heartbeat_queue = None

        self.socket = None
        self.is_alive = False

    def to_dict(self):
        return {
            "name": str(self.name),
            "answer": self.answer,
            "number_hand": self.number_hand,
            "tool_hand": self.tool_hand,
            "best_A": self.best_A,
            "best_B": self.best_B,
            "guess_histories": self.guess_histories,
        }

    @classmethod
    def from_dict(cls, data):
        player = cls(data.get("name", "Unknown"))
        player.answer = data.get("answer", [])
        player.number_hand = data.get("number_hand", [])
        player.tool_hand = data.get("tool_hand", [])
        player.best_A = data.get("best_A", 0)
        player.best_B = data.get("best_B", 0)
        player.guess_histories = data.get("guess_histories", [])
        return player

    def __str__(self):
        return self.name

    def set_socket(self, socket):
        self.socket = socket
