cmake_minimum_required(VERSION 3.16)

# Wi-Fi: з оточення або з wifi.cmake поруч (не комітити), напр.
#   set(WIFI_SSID "назва")
#   set(WIFI_PASSWORD "пароль")
include(${CMAKE_CURRENT_LIST_DIR}/wifi.cmake OPTIONAL)
if(DEFINED ENV{WIFI_SSID})
  set(WIFI_SSID "$ENV{WIFI_SSID}")
endif()
if(DEFINED ENV{WIFI_PASSWORD})
  set(WIFI_PASSWORD "$ENV{WIFI_PASSWORD}")
endif()
if(NOT DEFINED WIFI_SSID OR NOT DEFINED WIFI_PASSWORD)
  message(FATAL_ERROR "задай WIFI_SSID і WIFI_PASSWORD (оточення або firmware/wifi.cmake)")
endif()
add_compile_definitions(WIFI_SSID="${WIFI_SSID}" WIFI_PASSWORD="${WIFI_PASSWORD}")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(clap_light)
