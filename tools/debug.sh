#!/bin/bash
# Enable all StillPoint debug logging flags

export ZIMX_DEBUG_EDITOR=1
export ZIMX_DEBUG_NAV=1
export ZIMX_DEBUG_HISTORY=1
export ZIMX_DEBUG_PANELS=1
export ZIMX_DEBUG_TASKS=1
export ZIMX_DEBUG_PLANTUML=1
export ZIMX_DETAILED_PAGE_LOG=1
export ZIMX_DETAILED_LOGGING=1

# Run the application with all debug flags enabled
./real.sh
