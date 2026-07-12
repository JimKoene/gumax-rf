DOMAIN = "gumax_rf"

CONF_ESPHOME_NODE = "esphome_node"
CONF_DEVICE_ID = "device_id"
CONF_CHANNEL_PREFIX = "channel_prefix"

# Per-remote calibration (see DeviceProfile in _protocol.py). Learned via the
# capture_k1/capture_k2/capture_k9 config-flow steps; not user-editable.
CONF_X_DEV = "x_dev"
CONF_K1_EXTRA = "k1_extra"
CONF_K9_EXTRA = "k9_extra"
CONF_B9_DEFAULT = "b9_default"
CONF_B9_K1 = "b9_k1"
CONF_B9_K9 = "b9_k9"

DEFAULT_CHANNEL_PREFIX = "C"
MAX_PREFIX_LENGTH = 12

CHANNELS = list(range(1, 17))

RF_CAPTURE_EVENT = "esphome.gumax_rf_capture"
