"""Seed the execution-agent roster with a large fake fleet to demo the
agent-overload fix. Run: python seed_roster.py

Creates ~60 agents with plausible names/descriptions and varied recency,
so the selection layer has something real to select from.
"""

import random
from datetime import datetime, timedelta

from server.services.execution.roster import get_agent_roster

FIRST_NAMES = [
    "Alice", "Bob", "Carlos", "Dana", "Elena", "Farid", "Grace", "Hiro",
    "Ines", "Jamal", "Kira", "Liam", "Mona", "Nate", "Olga", "Priya",
]

TOPICS = [
    ("Email to {n}", "Handles the ongoing email thread with {n}"),
    ("Email to {n} re: invoice", "Follows up on the unpaid invoice discussion with {n}"),
    ("Flight to {city}", "Tracks flight options and booking for the {city} trip"),
    ("{n} birthday reminder", "Reminds about {n}'s birthday and gift ideas"),
    ("Weekly report to {n}", "Compiles and emails the weekly status report to {n}"),
    ("Lease renewal", "Handles the apartment lease renewal paperwork thread"),
    ("Gym membership cancellation", "Cancels the gym membership over email"),
    ("Conference talk submission", "Manages the CFP submission and follow-ups"),
    ("Insurance claim follow-up", "Chases the car insurance claim status"),
    ("Dentist appointment", "Schedules and confirms dental checkups"),
]

CITIES = ["Tokyo", "Lisbon", "Vancouver", "Austin", "Berlin"]


def main() -> None:
    roster = get_agent_roster()
    roster.load()
    created = 0

    for name in FIRST_NAMES:
        for template_name, template_desc in random.sample(TOPICS, k=4):
            agent_name = template_name.format(n=name, city=random.choice(CITIES))
            description = template_desc.format(n=name, city=random.choice(CITIES))
            if agent_name in roster.get_agents():
                continue
            roster.add_agent(agent_name, description=description)
            # Spread last_active over the past 30 days so recency matters.
            record = roster._find(agent_name)
            days_ago = random.randint(0, 30)
            record["last_active"] = (datetime.now() - timedelta(days=days_ago)).isoformat(
                timespec="seconds"
            )
            created += 1

    roster.save()
    print(f"Seeded {created} agents. Roster total: {len(roster.get_agents())}")


if __name__ == "__main__":
    main()
