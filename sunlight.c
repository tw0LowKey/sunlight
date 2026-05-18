#include <SPI.h>
#include <RH_RF95.h>
#include <RHReliableDatagram.h> // Required for Nodes/ACKs

#define RFM_CS 4
#define RFM_RST 2
#define RFM_IRQ 3

#define BASE_ADDRESS 0

RH_RF95 rf95(RFM_CS, RFM_IRQ);

// Object to manage message delivery and receipt
RHReliableDatagram manager(rf95, BASE_ADDRESS);

void setup() {
	while (!Serial);
	Serial.begin(115200);

	// Reset the radio module
	pinMode(RFM_RST, OUTPUT);
	digitalWrite(RFM_RST, HIGH);
	delay(10);
	digitalWrite(RFM_RST, LOW);
	delay(10);
	digitalWrite(RFM_RST, HIGH);
	delay(10);

	// If the radio module hasn't initialised, it has failed
	if (!manager.init()) {
		Serial.println("RADIO MODULE FAILED");
		while (1);
	}

	// Configure the radio module and the data transmission settings
	rf95.setFrequency(433.0);
	rf95.setPreambleLength(8); // Ensure CRC is enabled
	manager.setTimeout(1000);
	manager.setRetries(5);
	Serial.println("RADIO MODULE READY");
}

void loop() {
	// Python -> Arduino -> Radio (with automatic ACK) - Transmitting
 	if (Serial.available()) {
		String msg = Serial.readStringUntil('\n');
		uint8_t destinationNode = msg.substring(0, 2).toInt();
		msg = msg.substring(3); // 2 chars for the node + 1 char for the separating char
		uint8_t data[RH_RF95_MAX_MESSAGE_LEN + 1];
		msg.getBytes(data, RH_RF95_MAX_MESSAGE_LEN);

		// sendtoWait handles retries and waiting for an ACK
		if (manager.sendtoWait(data, msg.length(), destinationNode)) {
			// Success
		} else {
			Serial.println("No ACK received from Jetson");
		}
	}

	// Radio -> Arduino -> Python (with automatic ACK) - Receiving
	if (manager.available()) {
		uint8_t buf[RH_RF95_MAX_MESSAGE_LEN + 1];
		uint8_t len = sizeof(buf);
		uint8_t from;

		// recvfromAck automatically sends the ACK packet back
		if (manager.recvfromAck(buf, &len, &from)) {
			buf[len] = '\0'; // Null-terminate the string at the correct length
            Serial.print(from);
			Serial.print(":");
			Serial.println((char*)buf);
		}
	}
}
