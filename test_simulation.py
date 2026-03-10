"""Test script: simulates 2 agents joining, communicating, and tracking ideology."""

import random
import tempfile
from pathlib import Path

from src.db import init_db
from src import server

# Fix random seed so both agents land in the same society for DM testing
random.seed(42)


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

        # --- Ideology tracking via communications ---
        print("--- Ideology Tracking ---\n")

        # Alice sends left-leaning messages
        server.communicate(alice_id, "We need wealth redistribution and economic equality for all.", "public")
        server.communicate(alice_id, "Workers should collectively own the means of production.", "public")
        server.communicate(alice_id, "The government should provide universal healthcare and education.", "public")
        print("Alice sent 3 left-leaning messages.\n")

        # Bob sends right-leaning / authoritarian messages
        server.communicate(bob_id, "Free markets and private property are the foundation of prosperity.", "public")
        server.communicate(bob_id, "We need strong leadership and centralized authority to maintain order.", "public")
        server.communicate(bob_id, "Individual wealth creation through capitalism benefits everyone.", "public")
        print("Bob sent 3 right-leaning/authoritarian messages.\n")

        # Check ideology compass for each society
        print("--- Political Compass Results ---\n")
        for society_id in ["democracy_1", "oligarchy_1", "blank_slate_1"]:
            compass = server.get_ideology_compass(society_id)
            if "error" in compass:
                print(f"{society_id}: {compass['error']}\n")
            else:
                print(f"{society_id} ({compass['governance_type']}):")
                print(f"  Position: x={compass['x']}, y={compass['y']}")
                print(f"  Raw similarities: {compass['raw_similarities']}\n")

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
