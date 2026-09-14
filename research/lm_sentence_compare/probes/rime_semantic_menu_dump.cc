#include <dlfcn.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include <rime_api.h>

namespace {

constexpr const char* kFormatVersion = "qwen-semantic-menu-observation/v1";

std::string HexEncode(const char* value) {
  if (!value) {
    return "";
  }
  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (const unsigned char byte : std::string(value)) {
    output << std::setw(2) << static_cast<int>(byte);
  }
  return output.str();
}

std::vector<std::string> Split(const std::string& line, char separator) {
  std::vector<std::string> fields;
  size_t start = 0;
  while (true) {
    const size_t position = line.find(separator, start);
    if (position == std::string::npos) {
      fields.push_back(line.substr(start));
      return fields;
    }
    fields.push_back(line.substr(start, position - start));
    start = position + 1;
  }
}

bool LoadPlugin(const char* path) {
  if (dlopen(path, RTLD_NOW | RTLD_GLOBAL)) {
    return true;
  }
  std::cerr << "failed to load plugin " << path << ": " << dlerror() << '\n';
  return false;
}

void ConfigureSession(RimeApi* api, RimeSessionId session) {
  api->set_option(session, "ascii_mode", False);
  api->set_option(session, "extended_charset", True);
  api->set_option(session, "contextual_order", True);
  api->set_option(session, "context_reorder", True);
  api->set_option(session, "multi_short_code", False);
  api->set_option(session, "inflexible", False);
  api->set_option(session, "emoji", False);
  api->set_option(session, "quick_code_hint", False);
  api->set_option(session, "aux_hint", False);
  api->set_option(session, "chaifen", False);
  api->set_option(session, "pinyinhint", False);
  api->set_option(session, "unicode_comment", False);
}

RimeSessionId CreateSession(RimeApi* api, const std::string& schema_id) {
  const RimeSessionId session = api->create_session();
  if (!session || !api->select_schema(session, schema_id.c_str())) {
    if (session) {
      api->destroy_session(session);
    }
    return 0;
  }
  char selected_schema[256] = {};
  if (!api->get_current_schema(session, selected_schema, sizeof(selected_schema)) ||
      schema_id != selected_schema) {
    api->destroy_session(session);
    return 0;
  }
  ConfigureSession(api, session);
  return session;
}

bool EmitObservation(RimeApi* api,
                     RimeSessionId session,
                     const std::string& case_id,
                     const std::string& expected_raw,
                     int max_candidates,
                     std::ofstream* output) {
  const auto started = std::chrono::steady_clock::now();
  api->clear_composition(session);
  if (!api->set_input(session, expected_raw.c_str())) {
    return false;
  }

  const char* current_raw = api->get_input(session);
  if (!current_raw || expected_raw != current_raw) {
    std::cerr << "input mismatch for case " << case_id << '\n';
    return false;
  }

  RimeContext context = {};
  RIME_STRUCT_INIT(RimeContext, context);
  if (!api->get_context(session, &context)) {
    std::cerr << "failed to get context for case " << case_id << '\n';
    return false;
  }
  const RimeComposition& composition = context.composition;
  *output << "M\t" << case_id << '\t' << HexEncode(current_raw) << '\t'
          << HexEncode(composition.preedit) << '\t' << composition.length << '\t'
          << composition.cursor_pos << '\t' << composition.sel_start << '\t'
          << composition.sel_end << '\n';
  api->free_context(&context);

  int count = 0;
  bool truncated = false;
  RimeCandidateListIterator iterator = {};
  if (api->candidate_list_begin(session, &iterator)) {
    while (api->candidate_list_next(&iterator)) {
      if (count >= max_candidates) {
        truncated = true;
        break;
      }
      *output << "C\t" << case_id << '\t' << count << '\t'
              << HexEncode(iterator.candidate.text) << '\t'
              << HexEncode(iterator.candidate.comment) << '\n';
      ++count;
    }
    api->candidate_list_end(&iterator);
  }
  const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now() - started).count();
  *output << "E\t" << case_id << '\t' << count << '\t' << (truncated ? 1 : 0)
          << '\t' << elapsed << '\n';
  return output->good();
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 10) {
    std::cerr << "usage: rime_semantic_menu_dump LUA_PLUGIN OCTAGRAM_PLUGIN SHARED_DIR USER_DIR "
                 "SCHEMA_FILE SCHEMA_ID INPUT_FILE OUTPUT_FILE MAX_CANDIDATES\n";
    return 2;
  }

  if (!LoadPlugin(argv[1]) || !LoadPlugin(argv[2])) {
    return 3;
  }

  const std::string shared_dir = argv[3];
  const std::string user_dir = argv[4];
  const std::string schema_file = argv[5];
  const std::string schema_id = argv[6];
  const std::string input_file = argv[7];
  const std::string output_file = argv[8];
  const int max_candidates = std::atoi(argv[9]);
  if (max_candidates < 1) {
    std::cerr << "invalid candidate limit\n";
    return 2;
  }

  const std::string log_dir = user_dir + "/log";
  const std::string staging_dir = user_dir + "/build";
  std::filesystem::create_directories(log_dir);
  std::filesystem::create_directories(staging_dir);
  const char* modules[] = {"default", "deployer", "lua", "octagram", nullptr};

  RimeTraits traits = {};
  RIME_STRUCT_INIT(RimeTraits, traits);
  traits.shared_data_dir = shared_dir.c_str();
  traits.user_data_dir = user_dir.c_str();
  traits.distribution_name = "Mohu semantic menu observation";
  traits.distribution_code_name = "mohu-semantic-menu-observation";
  traits.distribution_version = "1";
  traits.app_name = "rime.mohu-semantic-menu-observation";
  traits.modules = modules;
  traits.min_log_level = 2;
  traits.log_dir = log_dir.c_str();
  traits.staging_dir = staging_dir.c_str();

  RimeApi* api = rime_get_api();
  api->setup(&traits);
  api->deployer_initialize(&traits);
  if (!api->deploy_config_file("default.yaml", "config_version") ||
      !api->deploy_schema(schema_file.c_str())) {
    std::cerr << "failed to deploy Rime configuration\n";
    return 4;
  }
  api->initialize(&traits);

  std::ifstream input(input_file);
  std::ofstream output(output_file);
  if (!input || !output) {
    std::cerr << "failed to open input or output file\n";
    api->finalize();
    return 6;
  }
  output << "H\t" << kFormatVersion << '\n';

  std::unordered_map<std::string, bool> seen_cases;
  std::string line;
  while (std::getline(input, line)) {
    if (line.empty()) {
      continue;
    }
    const auto fields = Split(line, '\t');
    if (fields.size() != 2 || fields[0].empty() || fields[1].empty() ||
        seen_cases.find(fields[0]) != seen_cases.end()) {
      std::cerr << "invalid or duplicate input row\n";
      api->finalize();
      return 7;
    }
    seen_cases.emplace(fields[0], true);
    const RimeSessionId session = CreateSession(api, schema_id);
    if (!session) {
      std::cerr << "failed to create session for schema: " << schema_id << '\n';
      api->finalize();
      return 5;
    }
    const bool emitted = EmitObservation(api, session, fields[0], fields[1],
                                         max_candidates, &output);
    api->destroy_session(session);
    if (!emitted) {
      std::cerr << "failed to emit menu observation for case " << fields[0] << '\n';
      api->finalize();
      return 8;
    }
  }

  if (input.bad()) {
    std::cerr << "failed while reading input stream\n";
    api->finalize();
    return 8;
  }
  output.flush();
  if (!output.good()) {
    std::cerr << "failed while writing output stream\n";
    api->finalize();
    return 8;
  }
  api->finalize();
  return 0;
}
