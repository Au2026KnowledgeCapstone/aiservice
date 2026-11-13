"""Engine-internal exceptions. Never include credentials or raw page content."""


class EngineError(Exception):
    """A condition the engine can name but cannot safely proceed past."""


class PolicyDenied(EngineError):
    """A browser operation left the approved origin or touched a blocked address."""


class ActionContractError(EngineError):
    """A proposed action does not compile against the current observation."""
