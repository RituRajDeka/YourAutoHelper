from app.edit_plan import EditPlan, Cut, SpeedChange
from app.plan_executor import map_transcript_to_composed

def test_mapping():
    edit_plan = EditPlan(
        cuts=[
            Cut(start_time=10.0, end_time=20.0),
            Cut(start_time=30.0, end_time=40.0)
        ],
        speed_changes=[
            SpeedChange(start_time=15.0, end_time=18.0, speed=2.0)
        ]
    )
    
    words = [
        # Outside cuts (before first cut) - should be discarded
        {"word": "discard1", "start": 5.0, "end": 6.0},
        # Inside Cut 1, before speed change
        {"word": "keep1", "start": 11.0, "end": 12.0},
        # Inside Cut 1, inside speed change
        {"word": "keep2", "start": 16.0, "end": 18.0},
        # Outside cuts (between cuts) - should be discarded
        {"word": "discard2", "start": 25.0, "end": 26.0},
        # Inside Cut 2, after speed change
        {"word": "keep3", "start": 32.0, "end": 34.0}
    ]
    
    mapped = map_transcript_to_composed(words, edit_plan, 50.0)
    print("Mapped words:")
    for w in mapped:
        print(f"Word: {w['word']}, Start: {w['start']:.3f}, End: {w['end']:.3f}")
        
    # Expected:
    # Segments in source timeline:
    # 1. [10.0, 15.0] -> speed 1.0. Output duration = 5.0s. Output range = [0.0, 5.0]
    # 2. [15.0, 18.0] -> speed 2.0. Output duration = 1.5s. Output range = [5.0, 6.5]
    # 3. [18.0, 20.0] -> speed 1.0. Output duration = 2.0s. Output range = [6.5, 8.5]
    # 4. [30.0, 40.0] -> speed 1.0. Output duration = 10.0s. Output range = [8.5, 18.5]
    
    # word "keep1" ([11.0, 12.0]): falls in Segment 1. Offset = 1.0s. Output range = [1.0, 2.0]
    # word "keep2" ([16.0, 18.0]): falls in Segment 2. Offset from 15.0 = 1.0s to 3.0s.
    # Start offset = 1.0s. Output start = 5.0 + 1.0 / 2.0 = 5.5s
    # End offset = 3.0s. Output end = 5.0 + 3.0 / 2.0 = 6.5s
    # word "keep3" ([32.0, 34.0]): falls in Segment 4. Offset from 30.0 = 2.0s to 4.0s.
    # Output range = [8.5 + 2.0, 8.5 + 4.0] = [10.5, 12.5]
    
    # Check assertions
    assert len(mapped) == 3
    assert mapped[0]["word"] == "keep1"
    assert abs(mapped[0]["start"] - 1.0) < 0.001
    assert abs(mapped[0]["end"] - 2.0) < 0.001
    
    assert mapped[1]["word"] == "keep2"
    assert abs(mapped[1]["start"] - 5.5) < 0.001
    assert abs(mapped[1]["end"] - 6.5) < 0.001
    
    assert mapped[2]["word"] == "keep3"
    assert abs(mapped[2]["start"] - 10.5) < 0.001
    assert abs(mapped[2]["end"] - 12.5) < 0.001
    
    print("All assertions passed!")

if __name__ == '__main__':
    test_mapping()
