#!/usr/bin/env bash
# CMake/Ninja timing and graph diagnostics that run inside minimal toolchain images.
set -uo pipefail

source_dir="."
build_dir="build"
generator="Ninja"
jobs="$CI_BUILD_JOBS"
if [[ -z "$jobs" ]]; then jobs=1; fi
defines=()

while (($#)); do
  case "$1" in
    --source-dir) source_dir="$2"; shift 2 ;;
    --build-dir) build_dir="$2"; shift 2 ;;
    --generator) generator="$2"; shift 2 ;;
    --build-jobs) jobs="$2"; shift 2 ;;
    --define) defines+=("-D$2"); shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
  echo "build jobs must be a positive integer: $jobs" >&2
  exit 2
fi

telemetry_dir="$source_dir/.ci-telemetry"
mkdir -p "$telemetry_dir"
profile="$telemetry_dir/cmake-configure-trace.json"
output="$telemetry_dir/cmake-build.json"
graph_file="$telemetry_dir/ninja-graph.dot"
commands_file="$telemetry_dir/ninja-commands.txt"
new_log_file="$telemetry_dir/ninja-log-delta.txt"

baseline_log_lines=0
if [[ -f "$build_dir/.ninja_log" ]]; then
  baseline_log_lines="$(wc -l < "$build_dir/.ninja_log")"
fi

start_ns="$(date +%s%N)"
configure_start="$start_ns"
cmake -S "$source_dir" -B "$build_dir" -G "$generator" \
  --profiling-format=google-trace \
  --profiling-output="$profile" \
  "${defines[@]}"
configure_rc=$?
configure_end="$(date +%s%N)"
configure_ms=$(( (configure_end - configure_start) / 1000000 ))

compile_rc=0
compile_ms=0
if [[ "$configure_rc" -eq 0 ]]; then
  compile_start="$(date +%s%N)"
  if [[ "$generator" == "Ninja" || "$generator" == "ninja" ]]; then
    cmake --build "$build_dir" --parallel "$jobs" -- -d stats -l "$jobs"
    compile_rc=$?
  else
    cmake --build "$build_dir" --parallel "$jobs"
    compile_rc=$?
  fi
  compile_end="$(date +%s%N)"
  compile_ms=$(( (compile_end - compile_start) / 1000000 ))
else
  compile_rc="$configure_rc"
fi

total_end="$(date +%s%N)"
total_ms=$(( (total_end - start_ns) / 1000000 ))
profile_events=0
if [[ -f "$profile" ]]; then
  profile_events="$(grep -o '"ph"' "$profile" | wc -l | tr -d ' ')"
fi

graph_available=false
graph_nodes=0
graph_edges=0
target_count=0
command_count=0
unique_command_count=0
repeated_command_lines=0
executed_edges=0
object_compile_edges=0
duplicate_output_edges=0
pending_actions=0
incremental_noop=false

if [[ "$generator" == "Ninja" || "$generator" == "ninja" ]] && command -v ninja >/dev/null 2>&1 && [[ -f "$build_dir/build.ninja" ]]; then
  graph_available=true
  if ninja -C "$build_dir" -t graph all > "$graph_file" 2> "$telemetry_dir/ninja-graph.stderr"; then
    graph_nodes="$(grep -Ec '^[[:space:]]*"?0x?[0-9a-fA-F]+"?[[:space:]]+\[' "$graph_file" || true)"
    graph_edges="$(grep -c ' -> ' "$graph_file" || true)"
  fi
  target_count="$(ninja -C "$build_dir" -t targets all 2>/dev/null | awk 'NF { count++ } END { print count+0 }')"
  if ninja -C "$build_dir" -t commands all > "$commands_file" 2>/dev/null; then
    command_count="$(awk 'NF { count++ } END { print count+0 }' "$commands_file")"
    unique_command_count="$(sort -u "$commands_file" | awk 'NF { count++ } END { print count+0 }')"
    repeated_command_lines=$((command_count - unique_command_count))
  fi
  if [[ -f "$build_dir/.ninja_log" ]]; then
    tail -n "+$((baseline_log_lines + 1))" "$build_dir/.ninja_log" > "$new_log_file" || true
    read -r executed_edges object_compile_edges duplicate_output_edges < <(
      awk -F '\t' '
        NF >= 5 && $1 !~ /^#/ {
          total++
          if ($4 ~ /\.(o|obj)$/) {
            object_total++
            seen[$4]++
          }
        }
        END {
          for (output in seen) unique++
          print total+0, object_total+0, object_total-unique
        }
      ' "$new_log_file"
    )
  fi
  pending_actions="$(ninja -C "$build_dir" -n 2>/dev/null | grep -Ec '^[[:space:]]*\[[0-9]+/[0-9]+\]' || true)"
  if [[ "$pending_actions" -eq 0 ]]; then incremental_noop=true; fi
fi

printf '{"schema_version":1,"generator":"%s","configure_generate_ms":%s,"compile_link_ms":%s,"total_ms":%s,"configure_exit_code":%s,"compile_exit_code":%s,"cmake_profile_event_count":%s,"ninja":{"available":%s,"graph_nodes":%s,"graph_edges":%s,"target_count":%s,"command_count":%s,"unique_command_count":%s,"repeated_command_lines":%s,"executed_edges":%s,"object_compile_edges":%s,"duplicate_output_edges":%s,"pending_actions_after_build":%s,"incremental_noop":%s}}\n' \
  "$generator" "$configure_ms" "$compile_ms" "$total_ms" "$configure_rc" "$compile_rc" "$profile_events" \
  "$graph_available" "$graph_nodes" "$graph_edges" "$target_count" "$command_count" "$unique_command_count" \
  "$repeated_command_lines" "$executed_edges" "$object_compile_edges" "$duplicate_output_edges" \
  "$pending_actions" "$incremental_noop" > "$output"

echo "cmake_telemetry configure_generate_ms=$configure_ms compile_link_ms=$compile_ms total_ms=$total_ms profile_events=$profile_events"
echo "ninja_graph_telemetry nodes=$graph_nodes edges=$graph_edges targets=$target_count executed_edges=$executed_edges object_compile_edges=$object_compile_edges duplicate_outputs=$duplicate_output_edges pending_actions=$pending_actions incremental_noop=$incremental_noop"
exit "$compile_rc"
