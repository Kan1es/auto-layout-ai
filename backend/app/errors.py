class StorageError(RuntimeError):
    pass

class JsonNotFoundError(StorageError):
    def __init__(self, path):
        super().__init__(f"json-файл не найден: {path}")
        self.path = path

class JsonReadError(StorageError):
    def __init__(self, path, error):
        super().__init__(f"json-файл не прочитался {path} : {error}")
        self.path = path

class JsonFormatError(StorageError):
    def __init__(self, path, line):
        super().__init__(f"json-некорректен {path}, строка - {line}")
        self.path = path
        self.line = line

class JsonWriteError(StorageError):
    def __init__(self, path, error):
        super().__init__(f"Не удалось сохранить json {path}, ошибка - {error}")
        self.path = path