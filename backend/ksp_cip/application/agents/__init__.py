"""The five agents. There are exactly five, and this module is the proof."""

from ...domain.enums import AgentName
from .base import AgentRequest, BaseAgent
from .crime_analytics import CrimeAnalyticsAgent
from .data_retrieval import DataRetrievalAgent
from .investigation_support import InvestigationSupportAgent
from .network_intelligence import NetworkIntelligenceAgent
from .supervisor import FOLLOW_UP_ROUTING, INTENT_ROUTING, SupervisorAgent, TurnRequest

#: Asserted by tests: the agent roster is closed.
AGENT_ROSTER = (
    AgentName.SUPERVISOR,
    AgentName.DATA_RETRIEVAL,
    AgentName.CRIME_ANALYTICS,
    AgentName.NETWORK_INTELLIGENCE,
    AgentName.INVESTIGATION_SUPPORT,
)

__all__ = [
    "AGENT_ROSTER", "AgentRequest", "BaseAgent", "CrimeAnalyticsAgent", "DataRetrievalAgent",
    "FOLLOW_UP_ROUTING", "INTENT_ROUTING", "InvestigationSupportAgent", "NetworkIntelligenceAgent",
    "SupervisorAgent", "TurnRequest",
]
