# hw/ — залізо й мережа (ESP-IDF). detect/ — чиста логіка без ESP-заголовків, щоб ганяти на Mac.
idf_component_register(
    SRCS "main.cpp" "hw/mic.cpp" "hw/net.cpp"
         "detect/level.cpp" "detect/ring.cpp" "detect/clap.cpp" "detect/clap_model.cpp" "detect/intent.cpp"
    INCLUDE_DIRS "." "hw" "detect")
