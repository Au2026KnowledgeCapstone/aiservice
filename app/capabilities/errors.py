class CapabilityError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status
