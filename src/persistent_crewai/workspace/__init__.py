"""
Workspace scaffolding, project isolation, and AST code indexing package.
"""

from persistent_crewai.workspace.project_scaffold import ProjectScaffold, DEFAULT_PROJECTS_DIR
from persistent_crewai.workspace.ast_mapper import ASTSymbolExtractor

__all__ = [
    "ProjectScaffold",
    "DEFAULT_PROJECTS_DIR",
    "ASTSymbolExtractor",
]
