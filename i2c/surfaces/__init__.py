"""i2c surfaces — transport adapters over ``i2c.control`` (§7.1).

Each surface is a thin driver: parse an inbound message, call exactly one
``i2c.control`` function, format the structured result for that medium. All
non-transport logic lives in the ``*_core`` modules so it is unit-testable
without the transport's optional dependency installed.
"""
