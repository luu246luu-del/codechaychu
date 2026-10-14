cmake_minimum_required(VERSION 3.18)
project(MagicLensGPU LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native")

find_package(Threads REQUIRED)
find_package(OpenCV REQUIRED COMPONENTS
  core imgproc imgcodecs videoio highgui
  cudaarithm cudafilters cudaimgproc)

if(NOT OpenCV_CUDA_VERSION)
  message(FATAL_ERROR "OpenCV was built without CUDA support (OpenCV_CUDA_VERSION is empty).")
endif()
message(STATUS "OpenCV ${OpenCV_VERSION}, CUDA ${OpenCV_CUDA_VERSION}")

add_executable(magic_lens main.cpp)
target_include_directories(magic_lens PRIVATE ${OpenCV_INCLUDE_DIRS})
target_link_libraries(magic_lens PRIVATE ${OpenCV_LIBS} Threads::Threads)
target_compile_options(magic_lens PRIVATE -Wall -Wextra)
