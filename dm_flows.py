"""Registry for DM flows to prevent message interference between concurrent wizards."""
import logging

log = logging.getLogger(__name__)

# Maps user_id -> active flow type ("edit", "attendance", "repetition", etc.)
_active_flows: dict[int, str] = {}


def register_flow(user_id: int, flow_type: str) -> bool:
    """Register a flow for a user. Returns False if another flow is already active."""
    current = _active_flows.get(user_id)
    if current and current != flow_type:
        log.warning("User %s has active flow '%s', cannot start '%s'", user_id, current, flow_type)
        return False
    _active_flows[user_id] = flow_type
    return True


def unregister_flow(user_id: int, flow_type: str):
    """Unregister a flow only if it matches the current one."""
    current = _active_flows.get(user_id)
    if current == flow_type:
        del _active_flows[user_id]


def get_active_flow(user_id: int) -> str | None:
    """Return the active flow type for a user, or None."""
    return _active_flows.get(user_id)


def make_check(author_id: int, flow_type: str):
    """Create a DM check function that only accepts messages for the given flow."""
    def check(m):
        if m.author.id != author_id or m.guild is not None:
            return False
        active = _active_flows.get(author_id)
        # Accept if no flow is registered (legacy/backward compat) or if it's this flow
        return active is None or active == flow_type
    return check
