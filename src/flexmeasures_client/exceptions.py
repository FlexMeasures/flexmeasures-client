class ContentTypeError(Exception):
    """Raised when the response from the API is not in the expected format"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class WrongHostError(Exception):
    """Raised when the host includes the scheme (http:// or https://)"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class EmptyPasswordError(Exception):
    """Raised when the password is empty"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class EmailValidationError(Exception):
    """Raised when the email is not in the correct format"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class WrongAPIVersionError(Exception):
    """Raised when the API version is not in the versions list"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
