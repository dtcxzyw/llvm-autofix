#! /bin/bash

# Check if our environment is set up or not
if [ -z "$LLVM_AUTOFIX_HOME_DIR" ]; then
    echo "Error: The llvm-autofix environment has not been brought up."
    exit 1
fi

# We run everything from the home directory
USER_WORKING_DIR=$(pwd)
cd $LLVM_AUTOFIX_HOME_DIR

show_usage() {
    echo "Usage: $0 <agent_name> [-D <model_driver>] [-m <model_name>] [-o <logs_dir>] [-R] [--help]"
    echo "  <agent_name>  Specify the agent name (autofix.mini or autofix.mswe)"
    echo "  -B    Specify the benchmark name (live or full; default: live)"
    echo "  -D    Specify the model driver (openai, anthropic, or openai_generic; default: openai_generic)"
    echo "  -m    Specify the model name (default: gpt-5)"
    echo "  -o    Specify directory saving logs (default: benchout/)"
    echo "  -R    Reset everything otherwise continue from last benchmarking state (default: false)"
    echo "  -C    Clean build directories after running each issue (default: false)"
    echo "  -h    Show this help message"
}

# Get current directory (bench/ directory)
BENCH_DIR=$LLVM_AUTOFIX_HOME_DIR/bench

# Parse options and arguments
if [ $# -lt 1 ]; then
    echo "Error: agent_name is required as a positional argument"
    show_usage
    exit 1
fi

AGENT_NAME="$1"
shift
if [[ "$AGENT_NAME" != "autofix.mini" && "$AGENT_NAME" != "autofix.mswe" ]]; then
    echo "Error: agent_name must be either 'autofix.mini' or 'autofix.mswe'"
    exit 1
fi

MODEL_DRIVER="openai_generic"
MODEL_NAME="gpt-5"
BENCH_NAME="live"
LOGGING_DIR="$LLVM_AUTOFIX_HOME_DIR/benchout"
RESET_FLAG="0"
CLEAN_FLAG="0"

while [[ $# -gt 0 ]]; do
    case $1 in
        -B|--benchmark)
            BENCH_NAME="$2"
            shift 2
            if [[ "$BENCH_NAME" != "live" && "$BENCH_NAME" != "full" ]]; then
                echo "Error: Benchmark name must be either 'live' or 'full'"
                exit 1
            fi
            ;;
        -D|--model-driver)
            MODEL_DRIVER="$2"
            if [[ "$MODEL_DRIVER" != "openai" && "$MODEL_DRIVER" != "openai_generic" && "$MODEL_DRIVER" != "anthropic" ]]; then
                echo "Error: MODEL_DRIVER must be one of 'openai' or 'anthropic'"
                exit 1
            fi
            shift 2
            ;;
        -m|--model-name)
            MODEL_NAME="$2"
            shift 2
            ;;
        -o|--output)
            LOGGING_DIR="$USER_WORKING_DIR/$2"
            if [ -z "$LOGGING_DIR" ]; then
                echo "Error: -o requires a directory path for saving benchmarking logs"
                exit 1
            fi
            shift 2
            ;;
        -R|--reset)
            RESET_FLAG="1"
            shift
            ;;
        -C|--clean)
            CLEAN_FLAG="1"
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Error: Unknown option $1"
            show_usage
            exit 1
            ;;
    esac
done

# Get the list of issues to process from the benchmark directory
mapfile -t ISSUE_LIST < <(find "$BENCH_DIR/$BENCH_NAME" -name "*.json" -exec basename {} .json \;)

PROCESSED_ISSUES_FILE="$LOGGING_DIR/processed_issues"
SUCCESS_DIR="$LOGGING_DIR/success"
FAILURE_DIR="$LOGGING_DIR/failure"

# Check if we need to reset everything
if [ "$RESET_FLAG" = "1" ]; then
    if [ -f "$PROCESSED_ISSUES_FILE" ]; then
        rm "$PROCESSED_ISSUES_FILE"
        echo "Processed issues file has been reset."
    else
        echo "No processed issues file found to reset."
    fi
    if [ -d "$SUCCESS_DIR" ]; then
        rm -rf "$SUCCESS_DIR"
        echo "Success logs directory has been reset."
    else
        echo "No success logs directory found to reset."
    fi
    if [ -d "$FAILURE_DIR" ]; then
        rm -rf "$FAILURE_DIR"
        echo "Failure logs directory has been reset."
    else
        echo "No failure logs directory found to reset."
    fi
fi

# Create log directories if they don't exist
mkdir -p "$SUCCESS_DIR"
mkdir -p "$FAILURE_DIR"

# Create processed_issues file if it doesn't exist
touch "$PROCESSED_ISSUES_FILE"

# Function to check if issue is already processed
is_issue_processed() {
    local issue=$1
    grep -q "^${issue}:" "$PROCESSED_ISSUES_FILE"
}

# Function to mark issue as processed
mark_issue_processed() {
    local issue=$1
    local status=$2
    echo "$issue:$status" >> "$PROCESSED_ISSUES_FILE"
}

# Function to generate timestamp in MMddhhmm format
get_timestamp() {
    date +"%m%d%H%M"
}

# Function to get model abbreviation
get_model_abbr() {
    case "$MODEL_NAME" in
        "gpt-5") echo "gpt5" ;;
        "gpt-4o") echo "gpt4o" ;;
        "gemini-2.5-pro") echo "gem25pro" ;;
        "gemini-3-pro-preview") echo "gem3prop" ;;
        "gemini-3-flash-preview") echo "gem3flashp" ;;
        "claude-sonnet-4.5") echo "sonnet45" ;;
        "deepseek-chat") echo "deepseekchat" ;;
        "qwen3-max") echo "qwen3max" ;;
        *) echo ${MODEL_NAME//[-.:]/} ;;
    esac
}

# Run either autofix.mini or autofix.mswe based on AGENT_NAME
run_agent() {
    local issue_id=$1
    local stats_file=$2
    # TODO: use git worktree for better isolation
    # Fix: in case the agent unexpectedly deleted the repository
    [ ! -d "$LAB_LLVM_DIR" ] && git clone https://github.com/llvm/llvm-project.git "$LAB_LLVM_DIR"
    if [ "$AGENT_NAME" = "autofix.mini" ]; then
        python -m autofix.mini --debug --model "$MODEL_NAME" --driver "$MODEL_DRIVER" --issue "$issue_id" --stats "$stats_file"
    elif [ "$AGENT_NAME" = "autofix.mswe" ]; then
        python -m autofix.mswe --debug --model "$MODEL_NAME" --issue "$issue_id" --stats "$stats_file"
    else
        echo "Error: Unknown agent name $AGENT_NAME"
        exit 1
    fi
}

# Function to run benchmark for a single issue
fix_issue() {
    local issue_id=$1
    local timestamp=$(get_timestamp)
    local model_abbr=$(get_model_abbr)
    local base_name="${issue_id}-${model_abbr}-${timestamp}"
    local json_name="${base_name}.json"
    local log_name="${base_name}.log"
    local traj_name="${base_name}.traj.json"
    local temp_json="/tmp/${json_name}"
    local temp_log="/tmp/${log_name}"
    local temp_traj="/tmp/${traj_name}"

    echo "Running issue $issue_id..."

    # Run the autofix command and capture both stdout and stderr
    if run_agent "$issue_id" "$temp_json" > "$temp_log" 2>&1; then
        # Command succeeded
        mv "$temp_json" "$SUCCESS_DIR/$json_name"
        mv "$temp_log" "$SUCCESS_DIR/$log_name"
        [ -f "$temp_traj" ] && mv "$temp_traj" "$SUCCESS_DIR/$traj_name"
        echo "✓ Issue $issue_id completed successfully - log saved to $SUCCESS_DIR/$base_name.*"
        mark_issue_processed "$issue_id" "success"
        return 0
    else
        # Command failed
        [ -f "$temp_json" ] && mv "$temp_json" "$FAILURE_DIR/$json_name"
        [ -f "$temp_log" ] && mv "$temp_log" "$FAILURE_DIR/$log_name"
        [ -f "$temp_traj" ] && mv "$temp_traj" "$FAILURE_DIR/$traj_name"
        echo "✗ Issue $issue_id failed - log saved to $FAILURE_DIR/$base_name.*"
        echo "   Last 15 lines of the log:"
        echo "   =========================="
        tail -n 15 "$FAILURE_DIR/$log_name" | sed 's/^/   /'
        echo "   =========================="
        mark_issue_processed "$issue_id" "failure"
        return 1
    fi
}

# Main benchmark execution
echo "Starting benchmark ..."
echo "  Agent: $AGENT_NAME"
echo "  Model: $MODEL_NAME ($MODEL_DRIVER)"
echo "  Reset: $RESET_FLAG"
echo "  Clean: $CLEAN_FLAG"
echo "  Bench: $BENCH_NAME"
echo "  Success logs will be saved to: $SUCCESS_DIR"
echo "  Failure logs will be saved to: $FAILURE_DIR"
echo "  Total issues to process: ${#ISSUE_LIST[@]}"
echo "  Processed issues file: $PROCESSED_ISSUES_FILE"
echo "==============================================="

success_count=0
failure_count=0
skipped_count=0
start_time=$(date +%s)

# Process each issue in the list
for issue in "${ISSUE_LIST[@]}"; do
    if is_issue_processed "$issue"; then
        echo "⏭ Issue $issue already processed - skipping"
        ((skipped_count++))
        continue
    elif fix_issue "$issue"; then
        ((success_count++))
    else
        ((failure_count++))
    fi

    rm -rf "$LLVM_AUTOFIX_HOME_DIR"/core.* >/dev/null 2>&1
    if [ "$CLEAN_FLAG" = "1" ]; then
      # Clean up build directories to save space
      rm -rf "$LAB_LLVM_BUILD_DIR/$issue" >/dev/null 2>&1
    fi

    # Sleep for a random time between 10-20 minutes before next issue
    # (except for the last issue) to avoid rate limiting
    if [ "$issue" != "${ISSUE_LIST[-1]}" ]; then
        sleep_minutes=$((RANDOM % 10 + 5))  # Random number between 5-15
        echo "Sleeping for $sleep_minutes minutes before next issue..."
        sleep ${sleep_minutes}m
    fi
    echo
done

# Print summary
end_time=$(date +%s)
duration=$((end_time - start_time))

echo "========================================"
echo "Benchmark completed!"
echo "Total issues in list: ${#ISSUE_LIST[@]}"
echo "Skipped (already processed): $skipped_count"
echo "Newly processed: $((success_count + failure_count))"
echo "Successful: $success_count"
echo "Failed: $failure_count"
if [ $((success_count + failure_count)) -gt 0 ]; then
    success_rate=$((success_count * 100 / (success_count + failure_count)))
    avg_time=$((duration / (success_count + failure_count)))
    echo "Success rate (newly processed): ${success_rate}%"
    echo "Average time per issue: ${avg_time} seconds"
fi
echo "Total runtime: ${duration} seconds"
