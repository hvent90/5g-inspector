"""Support interaction tracking service."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings
from ..models import SupportInteraction, SupportSummary

log = structlog.get_logger()


class SupportService:
    """Service for managing support interaction records."""

    def __init__(self) -> None:
        settings = get_settings()
        self._storage_path = Path(settings.database.path).parent / "support_interactions.json"
        self._interactions: list[SupportInteraction] = []
        self._load_interactions()

    def _load_interactions(self) -> None:
        """Load support interactions from file."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    data = json.load(f)
                self._interactions = [SupportInteraction(**item) for item in data]
                log.info("support_interactions_loaded", count=len(self._interactions))
            except Exception as e:
                log.error("support_interactions_load_failed", error=str(e))
                self._interactions = []

    def _save_interactions(self) -> None:
        """Save support interactions to file."""
        try:
            data = [i.model_dump(mode="json") for i in self._interactions]
            with open(self._storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error("support_interactions_save_failed", error=str(e))

    def get_all(self) -> list[dict]:
        """Get all support interactions, sorted by date descending."""
        interactions = [i.model_dump(mode="json") for i in self._interactions]
        # Sort by date descending (most recent first)
        return sorted(
            interactions,
            key=lambda x: (x.get("contact_date") or "", x.get("contact_time") or ""),
            reverse=True,
        )

    def get_by_id(self, interaction_id: str) -> dict | None:
        """Get a support interaction by ID."""
        for interaction in self._interactions:
            if interaction.id == interaction_id:
                return interaction.model_dump(mode="json")
        return None

    def create(self, data: dict[str, Any]) -> dict:
        """Create a new support interaction."""
        interaction = SupportInteraction(**data)
        self._interactions.append(interaction)
        self._save_interactions()
        log.info("support_interaction_created", id=interaction.id)
        return interaction.model_dump(mode="json")

    def update(self, interaction_id: str, updates: dict[str, Any]) -> dict | None:
        """Update an existing support interaction."""
        for i, interaction in enumerate(self._interactions):
            if interaction.id == interaction_id:
                # Update fields
                current_data = interaction.model_dump()
                current_data.update(updates)
                current_data["id"] = interaction_id  # Preserve original ID
                self._interactions[i] = SupportInteraction(**current_data)
                self._save_interactions()
                log.info("support_interaction_updated", id=interaction_id)
                return self._interactions[i].model_dump(mode="json")
        return None

    def delete(self, interaction_id: str) -> bool:
        """Delete a support interaction."""
        for i, interaction in enumerate(self._interactions):
            if interaction.id == interaction_id:
                del self._interactions[i]
                self._save_interactions()
                log.info("support_interaction_deleted", id=interaction_id)
                return True
        return False

    def get_summary(self) -> dict:
        """Get summary statistics of support interactions."""
        total = len(self._interactions)
        unresolved = sum(1 for i in self._interactions if i.resolution_status == "unresolved")

        # Calculate averages
        wait_times = [i.wait_time_minutes for i in self._interactions if i.wait_time_minutes]
        call_durations = [
            i.call_duration_minutes for i in self._interactions if i.call_duration_minutes
        ]
        total_transfers = sum(i.transfer_count for i in self._interactions)
        satisfaction_scores = [
            i.customer_satisfaction for i in self._interactions if i.customer_satisfaction
        ]

        return SupportSummary(
            total_interactions=total,
            unresolved_count=unresolved,
            avg_wait_time_minutes=(
                round(sum(wait_times) / len(wait_times), 1) if wait_times else None
            ),
            avg_call_duration_minutes=(
                round(sum(call_durations) / len(call_durations), 1) if call_durations else None
            ),
            total_transfers=total_transfers,
            avg_satisfaction=(
                round(sum(satisfaction_scores) / len(satisfaction_scores), 2)
                if satisfaction_scores
                else None
            ),
        ).model_dump()

    def export_for_fcc(self) -> dict:
        """Export support interactions in FCC complaint format."""
        interactions = self.get_all()
        summary = self.get_summary()

        # Generate narrative
        narrative_parts = []
        if summary["total_interactions"] > 0:
            narrative_parts.append(
                f"Over the course of dealing with service issues, I have contacted "
                f"T-Mobile support {summary['total_interactions']} times."
            )

            if summary["unresolved_count"] > 0:
                narrative_parts.append(
                    f"{summary['unresolved_count']} of these interactions remain unresolved."
                )

            if summary["avg_wait_time_minutes"]:
                narrative_parts.append(
                    f"Average wait time was {summary['avg_wait_time_minutes']} minutes."
                )

            if summary["total_transfers"] > 0:
                narrative_parts.append(
                    f"I was transferred between departments {summary['total_transfers']} times total."
                )

        narrative = " ".join(narrative_parts) if narrative_parts else "No support interactions logged."

        # Format for FCC
        formatted_interactions = []
        for interaction in interactions:
            formatted_interactions.append({
                "date": interaction.get("contact_date"),
                "time": interaction.get("contact_time"),
                "method": interaction.get("contact_method"),
                "agent": interaction.get("agent_name") or "Unknown",
                "ticket": interaction.get("ticket_number") or "None provided",
                "complaint": interaction.get("complaint_summary"),
                "response": interaction.get("response_received"),
                "resolution": interaction.get("resolution_offered"),
                "status": interaction.get("resolution_status"),
                "wait_minutes": interaction.get("wait_time_minutes"),
                "transferred": interaction.get("was_transferred"),
                "transfers": interaction.get("transfer_count"),
            })

        return {
            "summary": summary,
            "narrative": narrative,
            "interactions": formatted_interactions,
            "exported_at": datetime.utcnow().isoformat(),
        }


# Global service instance
_support_service: SupportService | None = None


def get_support_service() -> SupportService:
    """Get the global support service instance."""
    global _support_service
    if _support_service is None:
        _support_service = SupportService()
    return _support_service
