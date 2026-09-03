#include <dlfcn.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

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

std::string HexDecode(const std::string& hex) {
  if (hex.size() % 2 != 0) {
    return "";
  }
  std::string output;
  output.reserve(hex.size() / 2);
  for (size_t i = 0; i < hex.size(); i += 2) {
    try {
      const int high = std::stoi(hex.substr(i, 1), nullptr, 16);
      const int low = std::stoi(hex.substr(i + 1, 1), nullptr, 16);
      output.push_back(static_cast<char>((high << 4) | low));
    } catch (const std::exception&) {
      return "";
    }
  }
  return output;
}

bool LoadPlugin(const char* path) {
  if (dlopen(path, RTLD_NOW | RTLD_GLOBAL)) {
    return true;
  }
  std::cerr << "failed to load plugin " << path << ": " << dlerror() << '\n';
  return false;
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

enum class PrefixCommitResult {
  kUnavailable,
  kCommitted,
  kFatal,
};

PrefixCommitResult CommitPrefix(RimeApi* api,
                                RimeSessionId session,
                                const std::string& code,
                                const std::string& expected,
                                int* expected_index) {
  *expected_index = -1;
  if (code.empty() || expected.empty() || !api->set_input(session, code.c_str())) {
    return PrefixCommitResult::kFatal;
  }

  int seen = 0;
  RimeCandidateListIterator iterator = {};
  if (api->candidate_list_begin(session, &iterator)) {
    while (api->candidate_list_next(&iterator)) {
      if (expected == iterator.candidate.text) {
        *expected_index = seen;
        break;
      }
      if (++seen >= 40) {
        break;
      }
    }
    api->candidate_list_end(&iterator);
  }
  if (*expected_index < 0) {
    return PrefixCommitResult::kUnavailable;
  }
  if (!RIME_API_AVAILABLE(api, select_candidate) ||
      api->select_candidate(session, static_cast<size_t>(*expected_index)) != True) {
    return PrefixCommitResult::kFatal;
  }

  RimeCommit commit = {};
  RIME_STRUCT_INIT(RimeCommit, commit);
  if (!RIME_API_AVAILABLE(api, get_commit) || !api->get_commit(session, &commit)) {
    return PrefixCommitResult::kFatal;
  }
  const bool matches = commit.text && expected == commit.text;
  if (!RIME_API_AVAILABLE(api, free_commit)) {
    return PrefixCommitResult::kFatal;
  }
  api->free_commit(&commit);
  return matches ? PrefixCommitResult::kCommitted : PrefixCommitResult::kFatal;
}

bool EmitCandidates(RimeApi* api,
                    RimeSessionId session,
                    const std::string& case_id,
                    const char* mode,
                    const std::string& code,
                    int max_candidates,
                    std::ofstream* output) {
  const auto started = std::chrono::steady_clock::now();
  api->clear_composition(session);
  if (!api->set_input(session, code.c_str())) {
    return false;
  }

  int count = 0;
  bool truncated = false;
  RimeCandidateListIterator iterator = {};
  if (api->candidate_list_begin(session, &iterator)) {
    while (api->candidate_list_next(&iterator)) {
      if (count >= max_candidates) {
        truncated = true;
        break;
      }
      ++count;
      *output << "C\t" << case_id << '\t' << mode << '\t' << count << '\t'
              << HexEncode(iterator.candidate.text) << '\n';
    }
    api->candidate_list_end(&iterator);
  }
  const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now() - started).count();
  *output << "E\t" << case_id << '\t' << mode << '\t' << count << '\t'
          << (truncated ? 1 : 0) << '\t' << elapsed << '\n';
  return output->good();
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 11) {
    std::cerr << "usage: rime_wordgroup_dump LUA_PLUGIN OCTAGRAM_PLUGIN SHARED_DIR USER_DIR "
                 "SCHEMA_FILE SCHEMA_ID INPUT_FILE OUTPUT_FILE MAX_CANDIDATES "
                 "CONDITION(fresh|afterA)\n";
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
  const std::string condition = argv[10];
  const bool commit_prefix = condition == "afterA";
  if (max_candidates < 1 || (condition != "fresh" && !commit_prefix)) {
    std::cerr << "invalid candidate limit or condition\n";
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
  traits.distribution_name = "Mohu cross-candidate benchmark";
  traits.distribution_code_name = "mohu-cross-candidate-benchmark";
  traits.distribution_version = "2";
  traits.app_name = "rime.mohu-cross-candidate-benchmark";
  traits.modules = modules;
  traits.min_log_level = 2;
  traits.log_dir = log_dir.c_str();
  traits.staging_dir = staging_dir.c_str();

  RimeApi* api = rime_get_api();
  api->setup(&traits);
  api->deployer_initialize(&traits);
  if (!api->deploy_config_file("default.yaml", "config_version")) {
    std::cerr << "failed to deploy default.yaml\n";
    return 4;
  }
  if (!api->deploy_schema(schema_file.c_str())) {
    std::cerr << "failed to deploy schema: " << schema_file << '\n';
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

  RimeSessionId session = 0;
  std::string pending_case_id;
  std::string line;
  while (std::getline(input, line)) {
    if (line.empty()) {
      continue;
    }
    const auto fields = Split(line, '\t');
    if (fields.empty()) {
      continue;
    }

    if (fields[0] == "W") {
      if (!commit_prefix || fields.size() != 4 || fields[1].empty() || session) {
        std::cerr << "invalid W row or W/B ordering\n";
        api->finalize();
        return 7;
      }
      session = CreateSession(api, schema_id);
      if (!session) {
        std::cerr << "failed to create session for schema: " << schema_id << '\n';
        api->finalize();
        return 5;
      }
      pending_case_id = fields[1];
      int expected_index = -1;
      const PrefixCommitResult result = CommitPrefix(
          api, session, fields[2], HexDecode(fields[3]), &expected_index);
      if (result == PrefixCommitResult::kFatal) {
        std::cerr << "failed to commit prefix for case " << pending_case_id << '\n';
        api->destroy_session(session);
        api->finalize();
        return 8;
      }
      const bool ok = result == PrefixCommitResult::kCommitted;
      output << "A\t" << pending_case_id << '\t' << (ok ? 1 : 0) << '\t'
             << expected_index << '\n';
      api->clear_composition(session);
      continue;
    }

    if (fields[0] != "B" || fields.size() < 5 || fields[1].empty()) {
      std::cerr << "invalid input row\n";
      if (session) {
        api->destroy_session(session);
      }
      api->finalize();
      return 7;
    }
    if (commit_prefix) {
      if (!session || fields[1] != pending_case_id) {
        std::cerr << "afterA B row is not paired with its W row\n";
        if (session) {
          api->destroy_session(session);
        }
        api->finalize();
        return 7;
      }
    } else {
      if (session) {
        std::cerr << "unexpected live session before fresh B row\n";
        api->finalize();
        return 7;
      }
      session = CreateSession(api, schema_id);
      if (!session) {
        std::cerr << "failed to create session for schema: " << schema_id << '\n';
        api->finalize();
        return 5;
      }
    }

    // Legacy B rows are exactly nine fields carrying the fixed four-mode
    // layout (pure/head/tail/both).  Generic rows carry any number of
    // "<mode>=<code>" columns; mode names never contain '=' and codes are
    // letters plus an optional trailing 'o' or '/'.
    std::vector<std::pair<std::string, std::string>> mode_codes;
    bool legacy = fields.size() == 9;
    if (legacy) {
      static const char* kLegacyModes[] = {"pure", "head", "tail", "both"};
      for (int mode = 0; mode < 4; ++mode) {
        if (fields[mode + 2].find('=') != std::string::npos) {
          legacy = false;
          break;
        }
        mode_codes.emplace_back(kLegacyModes[mode], fields[mode + 2]);
      }
    }
    if (!legacy) {
      mode_codes.clear();
      // The final three columns are the hex target, a placeholder, and the
      // plain-text target; every column between the case id and them is one
      // mode column.
      const size_t mode_count = fields.size() - 5;
      if (mode_count == 0) {
        std::cerr << "B row carries no mode columns for case " << fields[1] << '\n';
        api->destroy_session(session);
        api->finalize();
        return 7;
      }
      for (size_t index = 0; index < mode_count; ++index) {
        const std::string& column = fields[2 + index];
        const size_t split_at = column.find('=');
        if (split_at == std::string::npos || split_at == 0 ||
            split_at + 1 == column.size()) {
          std::cerr << "invalid mode column '" << column << "' for case "
                    << fields[1] << '\n';
          api->destroy_session(session);
          api->finalize();
          return 7;
        }
        mode_codes.emplace_back(column.substr(0, split_at),
                                column.substr(split_at + 1));
      }
    }
    for (const auto& [mode, code] : mode_codes) {
      if (code.empty() || mode.empty()) {
        std::cerr << "empty mode code for case " << fields[1] << '\n';
        api->destroy_session(session);
        api->finalize();
        return 7;
      }
      if (!EmitCandidates(api, session, fields[1], mode.c_str(), code,
                          max_candidates, &output)) {
        std::cerr << "failed to emit candidates for case " << fields[1] << '\n';
        api->destroy_session(session);
        api->finalize();
        return 8;
      }
    }
    api->clear_composition(session);
    api->destroy_session(session);
    session = 0;
    pending_case_id.clear();
  }

  if (session || !pending_case_id.empty()) {
    std::cerr << "input ended between W and B rows\n";
    if (session) {
      api->destroy_session(session);
    }
    api->finalize();
    return 7;
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
