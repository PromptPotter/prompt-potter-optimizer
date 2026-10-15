# application/commands/ — the control-plane write path

**`CommandDispatcher` is the sole writer of `CommandRecord`** and of the ack of every command it applies itself; the check-in writes ride it too. **`dispatcher.py` is the pipeline and imports no handler**: a kind's row (`domain/command_kinds.py::CommandKind`) names its `module:handler`, imported when that kind is dispatched, so a verb that appends one record loads no launcher, scorer or client. A command only a running loop can take is acked `accepted` (or refused) here, and `applied` by the loop when it takes it.
