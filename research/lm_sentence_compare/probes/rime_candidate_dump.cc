#include <dlfcn.h>

#include <cstdlib>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

#include <rime_api.h>

namespace {

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

bool LoadPlugin(const char* path) {
  if (dlopen(path, RTLD_NOW | RTLD_GLOBAL)) {
    return true;
  }
  std::cerr << "failed to load plugin " << path << ": " << dlerror() << '\n';
  return false;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 10) {
    std::cerr << "usage: rime_candidate_dump LUA_PLUGIN OCTAGRAM_PLUGIN SHARED_DIR USER_DIR "
                 "SCHEMA_FILE SCHEMA_ID CODES_FILE OUTPUT_FILE MAX_CANDIDATES\n";
    return 2;
  }

  if (!LoadPlugin(argv[1]) || !LoadPlugin(argv[2])) {
    return 3;
  }

  const std::string shared_dir = argv[3];
  const std::string user_dir = argv[4];
  const std::string schema_file = argv[5];
  const std::string schema_id = argv[6];
  const std::string codes_file = argv[7];
  const std::string output_file = argv[8];
  const int max_candidates = std::atoi(argv[9]);
  if (max_candidates < 20) {
    std::cerr << "MAX_CANDIDATES must be at least 20 for Top-20 metrics\n";
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
  traits.distribution_name = "Mohu ranking audit";
  traits.distribution_code_name = "mohu-ranking-audit";
  traits.distribution_version = "1";
  traits.app_name = "rime.mohu-ranking-audit";
  traits.modules = modules;
  traits.min_log_level = 1;
  traits.log_dir = log_dir.c_str();
  traits.staging_dir = staging_dir.c_str();

  RimeApi* api = rime_get_api();
  api->setup(&traits);
  api->deployer_initialize(&traits);
  api->deploy_config_file("default.yaml", "config_version");
  if (!api->deploy_schema(schema_file.c_str())) {
    std::cerr << "failed to deploy schema: " << schema_file << '\n';
    return 4;
  }

  api->initialize(&traits);
  const RimeSessionId session = api->create_session();
  if (!session || !api->select_schema(session, schema_id.c_str())) {
    std::cerr << "failed to select schema: " << schema_id << '\n';
    api->finalize();
    return 5;
  }
  char selected_schema[256] = {};
  if (!api->get_current_schema(session, selected_schema, sizeof(selected_schema)) ||
      schema_id != selected_schema) {
    std::cerr << "schema selection mismatch: requested " << schema_id
              << ", active " << selected_schema << '\n';
    api->destroy_session(session);
    api->finalize();
    return 5;
  }

  api->set_option(session, "ascii_mode", False);
  // The corpus encoder permits every character present in the deployed
  // dictionary; keep the same full-character view for all three models.
  api->set_option(session, "extended_charset", True);
  api->set_option(session, "contextual_order", False);
  api->set_option(session, "multi_short_code", False);
  api->set_option(session, "inflexible", False);
  api->set_option(session, "emoji", False);
  api->set_option(session, "quick_code_hint", False);
  api->set_option(session, "aux_hint", False);
  api->set_option(session, "chaifen", False);
  api->set_option(session, "pinyinhint", False);
  api->set_option(session, "unicode_comment", False);

  std::ifstream codes(codes_file);
  std::ofstream output(output_file);
  if (!codes || !output) {
    std::cerr << "failed to open input or output file\n";
    api->destroy_session(session);
    api->finalize();
    return 6;
  }

  std::string line;
  while (std::getline(codes, line)) {
    if (line.empty()) {
      continue;
    }
    const size_t tab = line.find('\t');
    const std::string code = tab == std::string::npos ? line : line.substr(tab + 1);
    if (code.empty()) continue;
    const auto started = std::chrono::steady_clock::now();
    api->clear_composition(session);
    if (!api->set_input(session, code.c_str())) {
      const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now() - started).count();
      output << "E\t" << code << "\t0\t0\t" << elapsed << '\n';
      continue;
    }

    int count = 0;
    bool truncated = false;
    RimeCandidateListIterator iterator = {};
    if (api->candidate_list_begin(session, &iterator)) {
      while (api->candidate_list_next(&iterator)) {
        ++count;
        output << "C\t" << code << '\t' << count << '\t'
               << HexEncode(iterator.candidate.text) << '\t'
               << HexEncode(iterator.candidate.comment) << '\n';
        if (count >= max_candidates) {
          truncated = true;
          break;
        }
      }
      api->candidate_list_end(&iterator);
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();
    output << "E\t" << code << '\t' << count << '\t' << (truncated ? 1 : 0)
           << '\t' << elapsed << '\n';
  }

  api->clear_composition(session);
  api->destroy_session(session);
  api->finalize();
  return 0;
}
