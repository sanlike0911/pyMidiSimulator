# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python application that converts gamepad input into 14-bit MIDI CC (Control Change) messages. The application reads analog stick positions from connected gamepads and sends high-resolution MIDI data to virtual or physical MIDI devices.

## Core Architecture

### Main Components

- **`midi_simulator.py`** - Main application with `GamepadMidiController` class that handles:
  - Interactive MIDI port selection at startup
  - Interactive gamepad selection (when multiple are available)
  - Gamepad initialization and input processing via pygame
  - MIDI output via python-rtmidi
  - 14-bit CC message generation and transmission
  - Real-time input processing with deadzone handling

- **`setup.py`** - Automated setup script that checks dependencies, installs packages, and can launch the simulator

### MIDI Mapping

The application uses specific MIDI CC mappings for gamepad axes:
- Left stick: X→CC#16/48 (MSB/LSB), Y→CC#17/49 (MSB/LSB)
- Right stick: X→CC#18/50 (MSB/LSB), Y→CC#19/51 (MSB/LSB)
- 14-bit resolution: 0-16383 with neutral at 8192
- Transmission order: LSB first, then MSB
- Buttons: gamepad button i → CC#(20+i), i=0..9 (127=press / 0=release, threshold 64 on receiver)
- State selector: shoulder buttons (LB/RB) increment/decrement 0..16 → CC#30 (scaled to 0-127)

## Development Environment

**IMPORTANT: This project uses a Python virtual environment (venv). Always activate the virtual environment before running any Python commands.**

### Virtual Environment Setup (Required)
```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (macOS/Linux)
source .venv/bin/activate

# Deactivate
deactivate
```

### Virtual Environment Usage Rules
- **ALWAYS** activate `.venv` before running Python commands
- **NEVER** install packages globally - use the virtual environment
- Check if venv is active by looking for `(.venv)` in command prompt
- All development and testing should occur within the virtual environment

### Package Management
```bash
# Activate virtual environment first (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Check for missing dependencies
python setup.py
```

### Running the Application
```bash
# Activate virtual environment first (Windows)
.venv\Scripts\activate

# Direct execution
python midi_simulator.py

# Via setup script (interactive)
python setup.py
```

## Dependencies

- **pygame>=2.0.0** - Gamepad input handling
- **python-rtmidi>=1.4.0** - MIDI output
- **Python 3.7+** - Runtime requirement

## Key Configuration

- **DEADZONE**: Analog input threshold (default: 0.1)
- **Update rate**: 100Hz (10ms sleep in main loop)
- **MIDI CC numbers**: Defined as class constants in `GamepadMidiController`
- **14-bit range**: 0-16383 with neutral position at 8192

## User Interface Features

The application provides interactive device selection at startup:
- **Mode Selection**: At startup choose Normal mode (gamepad) or Demo mode. Demo mode needs no gamepad and continuously sends sticks (circular motion), buttons (sequential), and state (0↔16 sweep) for receiver-side testing.
- **MIDI Port Selection**: Lists available MIDI ports and allows user selection
- **Gamepad Selection**: Automatic selection for single gamepad, menu for multiple
- **Error Handling**: Graceful handling of device connection failures

## MIDI Device Setup

The application connects to existing MIDI ports. The README.md contains detailed setup instructions for loopMIDI and DAW configuration.

## Code Patterns

- Input processing uses change detection to minimize MIDI traffic
- 14-bit values are split into 7-bit MSB/LSB pairs
- Graceful cleanup of pygame and MIDI resources
- Exception handling for device initialization failures