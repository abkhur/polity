"""Test script: simulates 2 agents joining and interacting."""

import tempfile
from pathlib import Path

from src.db import init_db
from src import server


def run_simulation():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_polity.db"
        server.db = init_db(db_path)

        print("=== Polity Simulation Test ===\n")

        # Agent 1 joins
        result1 = server.join_society("Alice", consent=True)
        print(f"Alice joined: {result1}\n")

        # Agent 2 joins
        result2 = server.join_society("Bob", consent=True)
        print(f"Bob joined: {result2}\n")

        alice_id = result1["agent_id"]
        bob_id = result2["agent_id"]

        # Alice checks world state
        state = server.get_world_state(alice_id)
        print(f"Alice's world state: {state}\n")

        # Alice broadcasts a message
        comm = server.communicate(alice_id, "Hello everyone! I'm new here.", "public")
        print(f"Alice broadcast: {comm}\n")

        # Bob gathers resources
        gather = server.gather_resources(bob_id, 25)
        print(f"Bob gathered resources: {gather}\n")

        # Bob sends private message to Alice (only works if same society)
        if result1["society_id"] == result2["society_id"]:
            pm = server.communicate(bob_id, "Hey Alice, want to trade?", alice_id)
            print(f"Bob -> Alice DM: {pm}\n")
        else:
            print("Alice and Bob are in different societies, skipping DM.\n")

        # Alice checks state again to see communications
        state2 = server.get_world_state(alice_id)
        print(f"Alice's updated world state: {state2}\n")

        # Test consent guard
        no_consent = server.join_society("Eve", consent=False)
        print(f"Join without consent: {no_consent}\n")

        # Bob leaves
        leave = server.leave_society(bob_id, confirm=True)
        print(f"Bob left: {leave}\n")

        # Verify Bob is inactive
        try:
            server.get_world_state(bob_id)
        except ValueError as e:
            print(f"Bob's state after leaving (expected error): {e}\n")

        print("=== Simulation complete ===")


if __name__ == "__main__":
    run_simulation()
