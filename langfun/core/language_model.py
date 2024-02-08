# Copyright 2023 The Langfun Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Interface for language model."""

import abc
import dataclasses
import enum
import time
from typing import Annotated, Any
from langfun.core import component
from langfun.core import console
from langfun.core import message as message_lib
import pyglove as pg


class LMSample(pg.Object):
  """Response candidate."""

  response: pg.typing.Annotated[
      pg.typing.Object(
          message_lib.Message,
          # Allowing automatic conversion from text to AIMessage.
          transform=message_lib.AIMessage.from_value,
      ),
      'The natural language response of LM.',
  ]

  score: Annotated[
      float, 'The score of sampled response. The larger is better'
  ] = 0.0

  logprobs: Annotated[
      list[tuple[str, float, list[tuple[str, float]]]] | None,
      '(token, log prob, top tokens and their probs).',
  ] = None


class LMSamplingResult(pg.Object):
  """Language model response."""

  samples: Annotated[
      list[LMSample],
      (
          'Multiple samples of the same prompt, sorted by their score. '
          'The first candidate has the highest score.'
      ),
  ] = []


class LMSamplingOptions(component.Component):
  """Language model sampling options."""

  temperature: Annotated[
      float,
      (
          'Model temperature, which is usually between 0 and 1.0. '
          'OpenAI models have temperature range from 0.0 to 2.0.'
      )
  ] = 0.0
  max_tokens: Annotated[int, 'Per example max tokens to generate.'] = 1024
  n: Annotated[int | None, 'Max number of samples to return.'] = 1
  top_k: Annotated[
      int | None,
      (
          'Top k tokens to sample the next token. '
          'Not applicable to OpenAI models.'
      )
  ] = 40
  top_p: Annotated[
      float | None,
      (
          'Only sample the next token from top N tokens whose accumulated '
          'probability // mass <= p. For OpenAI models, set `temperature` or '
          '`top_p` but not both.'
      ),
  ] = None
  stop: Annotated[
      list[str] | None,
      (
          'A list of stop sequences that prevent LLMs from outputting '
          'more tokens. For example, when `stop` is set to ["User:", "Model:"] '
          'LLMs will stop to emit more tokens when `User:` or '
          '`Model:` is reached.'
      ),
  ] = None
  random_seed: Annotated[
      int | None, 'A fixed random seed used during model inference.'
  ] = None
  logprobs: Annotated[
      bool,
      (
          'Whether to return log probabilities of the output tokens or not. If '
          'true, returns the log probabilities of each output token returned '
          'in the content of message.'
      ),
  ] = False
  top_logprobs: Annotated[
      int | None,
      (
          'An integer between 0 and 5 specifying the number of most likely '
          'tokens to return at each token position, each with an associated '
          'log probability. logprobs must be set to true if this parameter is '
          'used.'
      ),
  ] = None

  def cache_key(self) -> tuple[Any, ...]:
    """Returns a tuple of current values as cache key."""
    return (
        self.temperature,
        self.max_tokens,
        self.n,
        self.top_k,
        self.top_p,
        self.random_seed
    )


class LMCache(pg.Object):
  """Interface for LM cache."""

  @dataclasses.dataclass
  class Stats:
    """Cache stats."""

    num_queries: int = 0
    num_hits: int = 0
    num_hit_expires: int = 0
    num_misses: int = 0
    num_updates: int = 0

  @abc.abstractmethod
  def get(
      self, lm: 'LanguageModel', prompt: message_lib.Message, seed: int
  ) -> LMSamplingResult | None:
    """Gets the cached result of a prompt generated by a language model."""

  @abc.abstractmethod
  def put(
      self,
      lm: 'LanguageModel',
      prompt: message_lib.Message,
      result: LMSamplingResult,
      seed: int,
  ) -> None:
    """Puts the result of a prompt generated by a language model in cache."""

  @property
  @abc.abstractmethod
  def stats(self) -> Stats:
    """Returns cache stats."""


class LMDebugMode(enum.IntFlag):
  """Sets debugging mode for a language model.

  INFO toggles whether information about the LM will be printed.
  PROMPT toggles whether the prompts sent to the LM will be printed.
  RESPONSE toggles whether the responses from the LM will be printed.
  """
  NONE = 0

  INFO = enum.auto()
  PROMPT = enum.auto()
  RESPONSE = enum.auto()

  @classmethod
  @property
  def ALL(cls) -> 'LMDebugMode':  # pylint: disable=invalid-name
    return LMDebugMode.INFO | LMDebugMode.PROMPT | LMDebugMode.RESPONSE


class LanguageModel(component.Component):
  """Interface of a language model.

  Language models are at the center of LLM-based agents. ``LanguageModel``
  is the interface to interact with different language modles.

  In langfun, users can use different language models with the same agents,
  allowing fast prototype, as well as side-by-side comparisons.
  """

  sampling_options: LMSamplingOptions = LMSamplingOptions()

  cache: Annotated[
      LMCache | None,
      (
          'Sampling cache. If None, no cache will be used.'
      )
  ] = component.contextual(default=None)

  timeout: Annotated[
      float | None, 'Timeout in seconds. If None, there is no timeout.'
  ] = 120.0

  max_attempts: Annotated[
      int,
      (
          'A number of max attempts to request the LM if fails.'
          'The retry wait time is determined per LM serivice.'
      ),
  ] = 5

  retry_interval: Annotated[
      int | tuple[int, int],
      (
          'An integer as a constant wait time in seconds before next retry, '
          'or a tuple of two integers representing the range of wait time, '
          'based on which the next wait time will be randmly chosen.'
      )
  ] = (5, 60)

  exponential_backoff: Annotated[
      bool,
      (
          'If True, the wait time among multiple attempts will exponentially '
          'grow. If `retry_interval` is an integer, the wait time for the '
          'k\'th attempt will be `retry_interval * 2 ^ (k - 1)` seconds. If '
          '`retry_interval` is a tuple, the wait time range for the k\'th '
          'attempt will be `(retry_interval[0] * 2 ^ (k - 1), '
          'retry_interval[1] * 2 ^ (k - 1)`) seconds.'
      )
  ] = True

  debug: Annotated[
      bool | LMDebugMode,
      (
          'If True, the prompt and the response will be output to stdout. '
          'Specific debugging fields (info, prompt, response) can be specified '
          'using the LMDebugMode flags.'
      ),
  ] = False

  @pg.explicit_method_override
  def __init__(self, *args, **kwargs) -> None:
    """Overrides __init__ to pass through **kwargs to sampling options."""

    sampling_options = kwargs.pop('sampling_options', LMSamplingOptions())
    sampling_options_delta = {}

    for k, v in kwargs.items():
      if LMSamplingOptions.__schema__.get_field(k) is not None:
        sampling_options_delta[k] = v

    if sampling_options_delta:
      sampling_options.rebind(sampling_options_delta)

    for k in sampling_options_delta:
      del kwargs[k]

    super().__init__(*args, sampling_options=sampling_options, **kwargs)

  def _on_bound(self):
    super()._on_bound()
    self._call_counter = 0

  @property
  def model_id(self) -> str:
    """Returns a string to identify the model."""
    return self.__class__.__name__

  @property
  def resource_id(self) -> str:
    """Resource ID for performing request parallism control."""
    return self.model_id

  @property
  def max_concurrency(self) -> int:
    """Max concurrent requests."""
    return 32

  def sample(
      self,
      prompts: list[str | message_lib.Message],
      *,
      cache_seed: int = 0,
      **kwargs,
  ) -> list[LMSamplingResult]:
    """Samples one or multiple prompts."""
    # Internal usage logging.

    prompts = [message_lib.UserMessage.from_value(p) for p in prompts]

    with component.context(override_attrs=True, **kwargs):
      if self.cache is None:
        return self._sample(prompts)
      else:
        return self._sample_with_cache_lookup(prompts, cache_seed)

  def _sample_with_cache_lookup(
      self, prompts: list[str | message_lib.Message], cache_seed: int
  ) -> list[LMSamplingResult]:
    """Sample with cache lookup."""
    assert self.cache is not None

    results = [None] * len(prompts)
    requests, request_to_result_index = [], {}

    # Perform cache lookup and figure out sampling requests to make.
    for i, prompt in enumerate(prompts):
      r = None
      # Query cache if cache_seed is not None.
      if cache_seed is not None:
        r = self.cache.get(self, prompt, seed=cache_seed)

      if r is None:
        request_to_result_index[len(requests)] = i
        requests.append(prompt)
      else:
        results[i] = r.clone()

    # Sample non-cache-hit prompts.
    if requests:
      requested_results = self._sample(requests)
      assert len(requested_results) == len(requests), (
          requests, requested_results)

      # Combine cached results and newly requested results.
      for i, (prompt, result) in enumerate(zip(requests, requested_results)):
        results[request_to_result_index[i]] = result

        # Carry the cache seed in response message.
        for sample in result.samples:
          sample.response.set('cache_seed', cache_seed)

        if cache_seed is not None:
          self.cache.put(self, prompt, result.clone(), seed=cache_seed)

    return results  # pytype: disable=bad-return-type

  @abc.abstractmethod
  def _sample(
      self,
      prompt: list[message_lib.Message],
  ) -> list[LMSamplingResult]:
    """Subclass should override."""

  def __call__(
      self, prompt: message_lib.Message, *, cache_seed: int = 0, **kwargs
  ) -> message_lib.Message:
    """Returns the first candidate."""
    prompt = message_lib.UserMessage.from_value(prompt)
    with component.context(override_attrs=True, **kwargs):
      sampling_options = self.sampling_options
      if sampling_options.n != 1:
        sampling_options = sampling_options.clone(override=dict(n=1))

      call_counter = self._call_counter
      self._call_counter += 1
      request_start = time.time()
      result = self.sample(
          [prompt], sampling_options=sampling_options, cache_seed=cache_seed
      )[0]
      response = result.samples[0].response
      logprobs = result.samples[0].logprobs
      response.set('score', result.samples[0].score)
      response.metadata.logprobs = logprobs
      elapse = time.time() - request_start
      self._debug(prompt, response, call_counter, elapse)
      return response

  def _debug(
      self,
      prompt: message_lib.Message,
      response: message_lib.Message,
      call_counter: int,
      elapse: float,
  ):
    """Outputs debugging information."""
    debug = self.debug
    if isinstance(debug, bool):
      debug = LMDebugMode.ALL if debug else LMDebugMode.NONE

    if debug & LMDebugMode.INFO:
      self._debug_model_info(call_counter)

    if debug & LMDebugMode.PROMPT:
      self._debug_prompt(prompt, call_counter)

    if debug & LMDebugMode.RESPONSE:
      self._debug_response(response, call_counter, elapse)

  def _debug_model_info(self, call_counter: int):
    """Outputs debugging information about the model."""
    console.write(
        self.format(compact=True, use_inferred=True),
        title=f'[{call_counter}] LM INFO:',
        color='magenta',
    )

  def _debug_prompt(self, prompt: message_lib.Message, call_counter: int):
    """Outputs debugging information about the prompt."""
    console.write(
        prompt,
        title=f'\n[{call_counter}] PROMPT SENT TO LM:',
        color='green',
    )
    referred_modalities = prompt.referred_modalities()
    if referred_modalities:
      console.write(
          pg.object_utils.kvlist_str(
              [(k, repr(v), None) for k, v in referred_modalities.items()]
          ),
          title=f'\n[{call_counter}] MODALITY OBJECTS SENT TO LM:',
          color='green',
      )

  def _debug_response(
      self, response: message_lib.Message, call_counter: int, elapse: float
  ):
    """Outputs debugging information about the response."""
    console.write(
        str(response) + '\n',
        title=f'\n[{call_counter}] LM RESPONSE (in {elapse:.2f} seconds):',
        color='blue',
    )
