"""``CommandDispatcher`` package — sole writer of ``CommandRecord`` at the API seam.

The dispatch / record / apply / ack pipeline lives in :mod:`.dispatcher`; the
free payload-coercion helpers + internal signal types live in :mod:`.helpers`.
Nothing is re-exported here — every consumer imports the leaf directly, e.g.
``from …middleware.command_dispatcher.dispatcher import CommandDispatcher``.
"""
