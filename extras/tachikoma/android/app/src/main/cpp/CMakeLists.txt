# タチコマの推論層: llama.cpp + JNI (tachikoma_llm.cpp)。設定は llama.cpp 公式の Android 例 (examples/llama.android) に合わせる
cmake_minimum_required(VERSION 3.22.1)
project("tachikoma_llm" C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED true)

if(NOT DEFINED LLAMA_DIR)
    set(LLAMA_DIR ${CMAKE_CURRENT_LIST_DIR}/../../../../llama.cpp)
endif()

if(ANDROID_ABI STREQUAL "arm64-v8a")
    set(GGML_SYSTEM_ARCH "ARM" CACHE STRING "" FORCE)
    set(GGML_OPENMP ON CACHE BOOL "" FORCE)
else()
    set(GGML_SYSTEM_ARCH "x86" CACHE STRING "" FORCE)
    set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
endif()

add_subdirectory(${LLAMA_DIR} build-llama)

add_library(tachikoma_llm SHARED tachikoma_llm.cpp)
target_link_libraries(tachikoma_llm llama ggml android log)
