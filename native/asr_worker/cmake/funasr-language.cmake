cmake_minimum_required(VERSION 3.19)

function(puripuly_patch_funasr_language)
    set(source "${CMAKE_SOURCE_DIR}/src/arch/funasr_nano/model.cpp")
    file(READ "${source}" original)
    string(REPLACE "\r\n" "\n" original "${original}")
    string(SHA256 source_hash "${original}")
    if(NOT source_hash STREQUAL "1aaa49925767d8b9e5dec2a6527ffba7f6bd7eb7fe69c2b19f35287f343fed3c")
        message(FATAL_ERROR "FunASR source changed: review the language-name patch against the pinned transcribe.cpp source")
    endif()

    set(patch_dir "${CMAKE_CURRENT_FUNCTION_LIST_DIR}")
    set(generated "${CMAKE_BINARY_DIR}/puripuly/funasr-model.cpp")
    string(REPLACE "out += lang;" "out += puripuly::funasr_language_name(lang);" patched "${original}")
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/puripuly")
    file(WRITE "${generated}" "#include \"${patch_dir}/funasr_language.h\"\n${patched}")
    get_target_property(sources transcribe SOURCES)
    list(FIND sources "arch/funasr_nano/model.cpp" source_index)
    if(source_index EQUAL -1)
        message(FATAL_ERROR "FunASR source target changed: language-name patch cannot be applied")
    endif()
    list(REMOVE_AT sources ${source_index})
    list(APPEND sources "${generated}")
    set_property(TARGET transcribe PROPERTY SOURCES "${sources}")
    set_source_files_properties("${generated}" DIRECTORY "${CMAKE_SOURCE_DIR}/src"
        PROPERTIES INCLUDE_DIRECTORIES "${CMAKE_SOURCE_DIR}/src/arch/funasr_nano")
    install(FILES "${patch_dir}/funasr_language.h" DESTINATION "share/puripuly-asr")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${source}" "${patch_dir}/funasr_language.h")
endfunction()

cmake_language(DEFER CALL puripuly_patch_funasr_language)

# --- CUDA BLAS delay-load (PuriPuly) -----------------------------------------
# ggml-cuda links CUDA::cublas (cublas64_N.dll, pulling cublasLt64_N.dll). The
# build script passes -DPURIPULY_CUBLAS_DELAYLOAD="<dll>;<dll>"; each is marked
# DELAYLOAD so loading ggml-cuda.dll -- and CUDA device discovery -- never maps
# the BLAS runtime. It binds on the first actual CUDA call: CPU/Vulkan startup
# pays nothing, and a missing cublas DLL degrades to "CUDA inference
# unavailable" instead of "backend module failed to load".
function(puripuly_cuda_delayload_cublas)
    if(NOT MSVC OR NOT TARGET ggml-cuda)
        return()
    endif()
    if(NOT DEFINED PURIPULY_CUBLAS_DELAYLOAD)
        message(FATAL_ERROR "PURIPULY_CUBLAS_DELAYLOAD must name the cublas DLLs to delay-load")
    endif()
    foreach(_cublas_dll IN LISTS PURIPULY_CUBLAS_DELAYLOAD)
        target_link_options(ggml-cuda PRIVATE "/DELAYLOAD:${_cublas_dll}")
    endforeach()
    target_link_libraries(ggml-cuda PRIVATE delayimp)
    message(STATUS "ggml-cuda delay-loads: ${PURIPULY_CUBLAS_DELAYLOAD}")
endfunction()

cmake_language(DEFER CALL puripuly_cuda_delayload_cublas)
