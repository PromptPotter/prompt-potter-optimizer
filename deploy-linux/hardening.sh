# Sourced, never run: the ONE systemd hardening block, shared by install-service.sh and
# install-admin-bot.sh so neither unit can drift weaker than the other.

# unit_hardening WRITE_PATH — the [Service] lines; WRITE_PATH is the one directory the unit may write.
unit_hardening() {
    cat <<EOF
# --- hardening (kernel-enforced blast-radius floor) ---------------------
# No privileges needed: drop every capability, deny privilege gain,
# and make the whole filesystem read-only except the ONE directory it writes.
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$1
UMask=0077
# kernel + process surface the app never touches
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
LockPersonality=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged ~@resources
EOF
}
