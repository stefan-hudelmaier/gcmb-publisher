import logging
import os
import queue
import time
from threading import Thread

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

logger = logging.getLogger()

broker = os.environ.get('MQTT_HOST', 'gcmb.io')
port = 8883
username = os.environ.get('MQTT_USERNAME')
password = os.environ.get('MQTT_PASSWORD')
client_id = os.environ.get('MQTT_CLIENT_ID', f'{username}/pub')


class MqttPublisher:

    def __init__(self, enable_watchdog=False, watchdog_minutes=10):

        self.mqtt_client = self._connect_mqtt()
        self.msg_queue = queue.Queue(maxsize=100000)
        self.start_time = time.time()
        self.last_successful_message = None
        self.watchdog_minutes = watchdog_minutes

        mqtt_publish_locations_thread = Thread(target=self._publish_msg_queue_messages, args=())
        mqtt_publish_locations_thread.start()

        if enable_watchdog:
            watchdog_thread = Thread(target=self._watchdog, args=())
            watchdog_thread.start()

        mqtt_client_thread = Thread(target=self._mqtt_client_thread, args=())
        mqtt_client_thread.start()

    def _mqtt_client_thread(self):
        self.mqtt_client.loop_forever(retry_first_connection=True)

    @staticmethod
    def _connect_mqtt():

        if not username or not password:
            logger.error("MQTT username or password not set, cannot connect to MQTT Broker")
            raise ValueError("MQTT username or password not set")

        def on_connect(client, userdata, flags, rc, properties):
            if rc == 0:
                logger.info("Connected to MQTT Broker")
            else:
                logger.error(f"Failed to connect, return code {rc}")

        mqtt_client = mqtt.Client(client_id=client_id,
                                  callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        mqtt_client.tls_set(ca_certs='/etc/ssl/certs/ca-certificates.crt')
        mqtt_client.username_pw_set(username, password)
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = lambda client, userdata, disconnect_flags, reason_code, properties: logger.warning(
            f"Disconnected from MQTT Broker, return code {reason_code}")
        mqtt_client.connect(broker, port)
        return mqtt_client

    @staticmethod
    def _create_publish_properties(message_expiry_interval):
        if message_expiry_interval is None:
            return None

        publish_properties = Properties(PacketTypes.PUBLISH)
        publish_properties.MessageExpiryInterval = message_expiry_interval
        return publish_properties


    def _publish(self, topic, msg, retain, message_expiry_interval=None):
        publish_properties = self._create_publish_properties(message_expiry_interval)
        result = self.mqtt_client.publish(topic, msg, retain=retain, properties=publish_properties)
        status = result.rc
        if status == 0:
            logger.debug(f"Sent '{msg}' to topic {topic} with id {result.mid}, retain {retain}, message expiry: {message_expiry_interval}. is_published: {result.is_published()}")
            return True
        else:
            logger.debug(f"Failed to send message to topic {topic}, reason: {status}")
            return False


    def _publish_msg_queue_messages(self):
        while True:

            msg, topic, retain, message_expiry_interval = self.msg_queue.get()
            try:

                successful_publish = self._publish(topic, msg, retain)
                if successful_publish:
                    self.last_successful_message = time.time()

            except Exception:
                logger.error(f"Exception publishing message to topic {topic}", exc_info=True)


    def send_msg(self, msg, topic, retain=False, message_expiry_interval=None):
        self.msg_queue.put((msg, topic, retain, message_expiry_interval, ))
        logger.debug(f"Message queued: {msg}")


    def _watchdog(self):
        while True:
            time.sleep(60)
            now = time.time()
            last_successful_message_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_successful_message)) if self.last_successful_message else "None"
            logger.info(f"Watchdog checking for inactivity: Last successful message was at {last_successful_message_str}")

            if self.last_successful_message is not None and now - self.last_successful_message > self.watchdog_minutes * 60:
                logger.error(f"No messages sent in last {self.watchdog_minutes} minutes, restarting")
                # sys.exit would not work in a thread
                os._exit(1)

            if self.last_successful_message is None and now - self.start_time > self.watchdog_minutes * 60:
                logger.error(f"No message has been sent yet and service is already running for {self.watchdog_minutes} minutes, restarting")
                os._exit(1)
