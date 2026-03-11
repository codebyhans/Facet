from __future__ import annotations

from dataclasses import dataclass

from photo_app.services.identity_cluster_service import TemporalIdentityClusterService


@dataclass(frozen=True)
class IdentityMaintenanceResult:
    """Summary from identity maintenance execution."""

    recalculated_clusters: int
    merged_clusters: int
    flagged_clusters: int


class IdentityMaintenanceJobs:
    """Periodic maintenance jobs for temporal identity clustering."""

    def __init__(self, service: TemporalIdentityClusterService) -> None:
        self._service = service

    def recalculate_cluster_centroids(self) -> int:
        """Recompute canonical embeddings and variance for all clusters."""
        return self._service.recalculate_all_cluster_states()

    def recalculate_temporal_embeddings(self) -> int:
        """Temporal embeddings are recomputed as part of recalculate_cluster_centroids."""
        return 0

    def detect_cluster_merges(self) -> int:
        """Find and merge highly similar clusters."""
        return self._service.detect_and_merge_duplicate_clusters()

    def monitor_cluster_variance(self) -> int:
        """Return number of clusters flagged for manual review."""
        flagged = self._service.list_clusters(flagged_only=True)
        return len(flagged)

    def run_all(self) -> IdentityMaintenanceResult:
        """Execute all maintenance tasks and return summary."""
        recalculated = self.recalculate_cluster_centroids()  # includes temporal embeddings
        merged = self.detect_cluster_merges()
        flagged = self.monitor_cluster_variance()
        return IdentityMaintenanceResult(
            recalculated_clusters=recalculated,
            merged_clusters=merged,
            flagged_clusters=flagged,
        )
