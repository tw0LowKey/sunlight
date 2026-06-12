# Sunlight

Sunlight is the custom, full-stack user interface designed to serve as the central command unit for the SAPLInG system. It provides a web-based dashboard for real-time monitoring and control.

## Features

- **Real-time Dashboard**: Built with Flask and Socket.IO for live updates on robot status, position, and battery levels.
- **Robot Provisioning**: Uses Bluetooth Low Energy (BLE) to securely provision robots.
- **Long-range Communication**: Leverages LoRa for reliable communication with robots over long distances.
- **Litter Tracking**: Allows users and robots to mark and track litter locations on a map, with data persisted locally.
- **Map Interface**: Uses Leaflet for interactive map visualisation.

## Project Structure

- `app.py`: The main Flask application server handling web routes, Socket.IO events, and high-level robot management logic.
- `comms.py`: A communication library containing classes for Bluetooth (`Bluetooth`) and LoRa (`Lora`) management.
- `requirements.txt`: List of Python dependencies.
- `static/`: Frontend assets including CSS, JavaScript (Leaflet, Socket.IO), and images.
- `templates/`: HTML templates for the dashboard and error pages.
- `sunlight.c`: Firmware for the Arduino transceiver.

## Installation & Setup

1.  Clone the repository:
    ```bash
    git clone https://github.com/tw0LowKey/sunlight
    cd sunlight
    ```

2.  Install the dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  Set the `FLASK_SECRET_KEY` environment variable before running the application:
    ```bash
    export FLASK_SECRET_KEY="your-secret-key"
    ```

4.  An Arduino is connected to the operator's laptop to communicate with the Adafruit RFM9x LoRa module. Connect the Arduino to the laptop via a USB and connect the RFM9x LoRa module to the Arduino - an Arduino Mega 2560 is shown in the diagram below:
    ![Arduino Mega 2560 Wiring Diagram](wiring_diagram.svg)

## Usage

Start the Flask application:
```bash
python app.py
```
The dashboard will be available at `http://localhost:5000` by default.
