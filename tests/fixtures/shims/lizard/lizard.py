# Simulates an absent lizard installation: importing this module fails the way
# a missing dependency does, so the CLI's missing-tool exit path can be exercised.
raise ImportError("simulated missing lizard")
