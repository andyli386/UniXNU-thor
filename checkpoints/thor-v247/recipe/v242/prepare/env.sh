# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Build inputs that are not part of any version recipe: the pinned public Wi-Fi
# sources (configs/pong-native-sources.toml) and their tools.
export PONG_WIFI_SOURCE=${THOR_ROOT}/UniXNU-pong/refer/ipados18/linux-7.0.14
export PONG_SHENG_ROOT=${THOR_ROOT}/UniXNU-pong/refer/ipados18/UniXNU-sheng
export PONG_WIFI_BUILD=${THOR_ROOT}/thor-out/v242-unified/build-tree/build/pong-ath11k
export PONG_LLVM_NM=$(xcrun --find llvm-nm)
export PONG_LLVM_OBJCOPY=/opt/homebrew/opt/llvm/bin/llvm-objcopy
export SDKROOT=$(xcrun --sdk iphoneos --show-sdk-path)
# (since v237) the unattended Wi-Fi association fixture reads the private network from this file
# ({"ssid": ..., "psk": ...}).  It stays out of boot args, logs and source control; absent: nothing is joined.
export PONG_WIFI_TEST_CONFIG=${THOR_ROOT}/thor-out/wifi-test-private.json
