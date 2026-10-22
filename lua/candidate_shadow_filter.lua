local MAX_WRAPPER_DEPTH = 32

local function unwrap(candidate)
  local current = candidate
  local depth = 0

  while depth < MAX_WRAPPER_DEPTH and current.get_dynamic_type and current.get_genuine do
    local candidate_type = current:get_dynamic_type()
    if candidate_type ~= "Shadow" then
      break
    end
    current = current:get_genuine()
    depth = depth + 1
  end

  return current, depth
end

local function filter(input)
  for cand in input:iter() do
    local genuine, depth = unwrap(cand)
    if depth > 1 and genuine and ShadowCandidate then
      local flattened = ShadowCandidate(genuine, cand.type, cand.text, cand.comment)
      flattened.quality = cand.quality
      yield(flattened)
    else
      yield(cand)
    end
  end
end

return {
  func = filter,
}
