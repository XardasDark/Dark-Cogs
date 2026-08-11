"""
storage.py – Persistenz-Schicht über Reds Config

Kapselt alle Lese-/Schreibzugriffe auf die Guild-Config. Umfragen und Antworten
werden pro Guild gespeichert:
  surveys   = {slug: survey_dict}
  responses = {slug: {user_id(str): {question_id: answer}}}
"""

import random
import re
from typing import Any, Dict, List, Optional

import discord

from .constants import SLUG_ALPHABET, SLUG_LENGTH


class SurveyStore:
    """Dünne, async Zugriffsschicht auf die Guild-Config."""

    def __init__(self, config):
        self.config = config

    # ── Einstellungen ─────────────────────────────────────────────────────────

    async def get_manager_role_id(self, guild: discord.Guild) -> Optional[int]:
        return await self.config.guild(guild).manager_role_id()

    async def set_manager_role_id(self, guild: discord.Guild, role_id: Optional[int]) -> None:
        await self.config.guild(guild).manager_role_id.set(role_id)

    async def get_results_channel_id(self, guild: discord.Guild) -> Optional[int]:
        return await self.config.guild(guild).results_channel_id()

    async def set_results_channel_id(self, guild: discord.Guild, channel_id: Optional[int]) -> None:
        await self.config.guild(guild).results_channel_id.set(channel_id)

    # ── Umfragen ──────────────────────────────────────────────────────────────

    async def all_surveys(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).surveys()

    async def get_survey(self, guild: discord.Guild, slug: str) -> Optional[Dict[str, Any]]:
        surveys = await self.config.guild(guild).surveys()
        return surveys.get(slug)

    async def save_survey(self, guild: discord.Guild, survey: Dict[str, Any]) -> None:
        async with self.config.guild(guild).surveys() as surveys:
            surveys[survey["id"]] = survey

    async def delete_survey(self, guild: discord.Guild, slug: str) -> None:
        async with self.config.guild(guild).surveys() as surveys:
            surveys.pop(slug, None)
        async with self.config.guild(guild).responses() as responses:
            responses.pop(slug, None)
        async with self.config.guild(guild).archives() as archives:
            archives.pop(slug, None)

    async def slug_exists(self, guild: discord.Guild, slug: str) -> bool:
        surveys = await self.config.guild(guild).surveys()
        return slug in surveys

    async def generate_slug(self, guild: discord.Guild) -> str:
        """Erzeugt eine kurze, in der Guild eindeutige Zufalls-ID."""
        surveys = await self.config.guild(guild).surveys()
        for _ in range(50):
            slug = "".join(random.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH))
            if slug not in surveys:
                return slug
        # Fallback: eine Stelle länger
        while True:
            slug = "".join(random.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH + 1))
            if slug not in surveys:
                return slug

    _UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

    @classmethod
    def normalize_slug(cls, name: str) -> str:
        """Wandelt einen Wunschnamen in einen sauberen Slug um (klein, kebab)."""
        slug = name.strip().lower()
        for src, dst in cls._UMLAUTS.items():
            slug = slug.replace(src, dst)
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug[:32]

    # ── Antworten ─────────────────────────────────────────────────────────────

    async def get_responses(self, guild: discord.Guild, slug: str) -> Dict[str, Dict[str, Any]]:
        responses = await self.config.guild(guild).responses()
        return responses.get(slug, {})

    async def save_response(self, guild: discord.Guild, slug: str, user_id: int, answer: Dict[str, Any]) -> None:
        async with self.config.guild(guild).responses() as responses:
            responses.setdefault(slug, {})[str(user_id)] = answer

    async def has_responded(self, guild: discord.Guild, slug: str, user_id: int) -> bool:
        responses = await self.config.guild(guild).responses()
        return str(user_id) in responses.get(slug, {})

    async def response_count(self, guild: discord.Guild, slug: str) -> int:
        responses = await self.config.guild(guild).responses()
        return len(responses.get(slug, {}))

    async def clear_responses(self, guild: discord.Guild, slug: str) -> None:
        async with self.config.guild(guild).responses() as responses:
            responses.pop(slug, None)

    # ── Archiv (vergangene Runden) ────────────────────────────────────────────

    async def get_archives(self, guild: discord.Guild, slug: str) -> List[Dict[str, Any]]:
        archives = await self.config.guild(guild).archives()
        return archives.get(slug, [])

    async def append_archive(self, guild: discord.Guild, slug: str, entry: Dict[str, Any]) -> None:
        async with self.config.guild(guild).archives() as archives:
            archives.setdefault(slug, []).append(entry)

    # ── Berechtigungen ────────────────────────────────────────────────────────

    @staticmethod
    def may_participate(survey: Dict[str, Any], member: discord.Member) -> bool:
        """True, wenn das Mitglied an der Umfrage teilnehmen darf (Allowlist)."""
        allowed_users: List[int] = survey.get("allowed_user_ids", [])
        allowed_roles: List[int] = survey.get("allowed_role_ids", [])
        if not allowed_users and not allowed_roles:
            return True  # keine Beschränkung → alle dürfen
        if member.id in allowed_users:
            return True
        member_role_ids = {r.id for r in member.roles}
        return bool(member_role_ids & set(allowed_roles))
