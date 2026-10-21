"""
Persistence package for Persistent CrewAI framework.
Provides SQLite database schema and connection management, state management,
vector storage with hash embeddings, session resumption, and state export/import.
"""

from persistent_crewai.persistence.db import (
    DatabaseManager,
    get_connection,
    init_db,
    SCHEMA_DDL,
)
from persistent_crewai.persistence.state_manager import (
    StateManager,
    ProjectMetadata,
    DecisionRecord,
    WorkflowRun,
    TaskHistoryRecord,
    MilestoneRecord,
    AgentStatusRecord,
    ExecutionTraceRecord,
    PersistenceError,
    InvalidRoleError,
    ProjectNotFoundError,
)
from persistent_crewai.persistence.vector_store import (
    EmbeddingProviderInterface,
    DeterministicHashEmbeddingProvider,
    SQLiteVectorStore,
    SearchResult,
)
from persistent_crewai.persistence.session import (
    SessionResumptionManager,
    WorkflowResumePlan,
    MilestoneResumePlan,
    ResumptionContext,
    SessionNotFoundError,
    StateImportConflictError,
    SchemaVersionMismatchError,
    StateIntegrityError,
)

__all__ = [
    # DB
    "DatabaseManager",
    "get_connection",
    "init_db",
    "SCHEMA_DDL",
    # State Manager
    "StateManager",
    "ProjectMetadata",
    "DecisionRecord",
    "WorkflowRun",
    "TaskHistoryRecord",
    "MilestoneRecord",
    "AgentStatusRecord",
    "ExecutionTraceRecord",
    "PersistenceError",
    "InvalidRoleError",
    "ProjectNotFoundError",
    # Vector Store
    "EmbeddingProviderInterface",
    "DeterministicHashEmbeddingProvider",
    "SQLiteVectorStore",
    "SearchResult",
    # Session
    "SessionResumptionManager",
    "WorkflowResumePlan",
    "MilestoneResumePlan",
    "ResumptionContext",
    "SessionNotFoundError",
    "StateImportConflictError",
    "SchemaVersionMismatchError",
    "StateIntegrityError",
]
