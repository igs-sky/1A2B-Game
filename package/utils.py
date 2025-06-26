def safe_call(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print("Exception in %s: %s" % (func.__name__, e))
            return None
    return wrapper