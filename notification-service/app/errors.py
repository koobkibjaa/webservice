# Errors leave this service in the monolith's shape, { "error": ..., "requestId": ... }, so web
# pages and the mobile app that already parse it keep working after the route moved.
class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message
