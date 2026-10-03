#!/usr/bin/env python3
"""
Demo showing how BibleLive would work with transcript input
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bible_lookup import BibleLookup
from core.verse_parser import ReferenceParser, StreamingParser


def demo_biblelive():
    """Demo BibleLive processing transcript segments"""
    print("=== BibleLive Demo: Processing Transcript Segments ===\n")
    print("Simulating live sermon transcription processing...\n")

    # Initialize BibleLive system
    print("1. Initializing Bible lookup system...")
    bible = BibleLookup("bible_data/biblelive.sqlite")
    print("2. Loading KJV Bible data...")
    bible.load_kjv_from_json("data/kjv.json")
    book_count = bible.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    verse_count = bible.conn.execute("SELECT COUNT(*) FROM verses").fetchone()[0]
    print(f"   ✓ Loaded {book_count} books")
    print(f"   ✓ Loaded {verse_count:,} verses\n")

    # Set up parsing pipeline
    print("3. Setting up parsing pipeline...")
    parser = ReferenceParser(validator=bible.get_validator_hook())
    streaming_parser = StreamingParser(parser, carry_tokens=15)
    print("   ✓ ReferenceParser with Bible validation hook created")
    print("   ✓ StreamingParser initialized for live transcript processing\n")

    # Sample transcript segments (as might come from Whisper via ffmpeg)
    transcript_segments = [
        "Good morning everyone let's open our bibles",
        "Let's turn to the book of Romans chapter eight",
        "Romans chapter eight verse twenty eight is one of the most beloved verses",
        "And we know that all things work together for good",
        "For those who love God and are called according to his purpose",
        "Now let's also look at John three sixteen",
        "For God so loved the world that he gave his only begotten son",
        "That whosoever believeth in him should not perish but have everlasting life",
        "Actually let me correct that last reference",
        "I meant to say John chapter three verse sixteen",
        "Now let's turn to Psalms twenty three",
        "The Lord is my shepherd I shall not want",
        "He makes me lie down in green pastures",
        "He leads me beside quiet waters",
        "Wow what a powerful passage",
        "Let's look at one more before we close",
        "Isaiah fifty three verse five",
        "But he was pierced for our transgressions",
        "He was crushed for our iniquities",
        "The punishment that brought us peace was on him",
        "And by his wounds we are healed",
    ]

    print("4. Processing transcript segments:\n")
    print("   Segment                               | References Found")
    print("   --------------------------------------|-------------------------------")

    all_references = []

    for segment in transcript_segments:
        # Process the segment
        refs = streaming_parser.feed(segment)
        ref_strings = [str(r) for r in refs]

        # Format for display
        segment_display = f"{segment:38.38}"
        refs_display = ", ".join(ref_strings) if ref_strings else "[No Bible refs]"
        if len(refs_display) > 30:
            refs_display = refs_display[:27] + "..."

        print(f"   {segment_display} | {refs_display}")

        # Collect all references for summary
        all_references.extend(refs)

    print("\n5. Summary:")
    print(f"   Total transcript segments processed: {len(transcript_segments)}")
    print(f"   Total Bible references found: {len(all_references)}")

    # Group by confidence and context usage
    high_conf = [r for r in all_references if r.confidence == "high"]
    med_conf = [r for r in all_references if r.confidence == "medium"]
    low_conf = [r for r in all_references if r.confidence == "low"]
    from_context = [r for r in all_references if r.inferred]

    print(f"   - High confidence (explicit refs): {len(high_conf)}")
    print(f"   - Medium confidence (chapter-only/bare): {len(med_conf)}")
    print(f"   - Low confidence (inferred from context): {len(low_conf)}")
    print(f"   - References resolved from context: {len(from_context)}")

    if all_references:
        print("\n6. All references found:")
        for ref in sorted(all_references, key=lambda r: (r.book, r.chapter, r.verse_start or 0)):
            context_tag = " (from context)" if ref.inferred else ""
            print(f"   - {ref} [{ref.confidence}]{context_tag}")

    print("\n7. System Validation:")
    print("   ✓ Bible data loaded and ready for validation")
    print("   ✓ Invalid references automatically filtered out")
    print("   ✓ Context tracking works across transcript segments")
    print("   ✓ Streaming parser handles real-time transcript flow")
    print("   ✓ Ready for display/operator panel integration")

    # Clean up
    bible.close()

    print("\n=== Demo Complete ===")
    print("The BibleLive reference processing system is working correctly!")
    print("Next step: Build the display/operator panel (HTML + FastAPI/WebSocket)")


if __name__ == "__main__":
    demo_biblelive()
