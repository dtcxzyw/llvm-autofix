import random

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec


class GetWeather(FuncToolBase):
  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "get_weather",
      "Fetch the current weather (weather, temperature, wind speed, etc) for a specified location at a specified date with a specified unit.",
      [
        FuncToolSpec.Param(
          "location", "string", True, "The name of the location to get the weather for."
        ),
        FuncToolSpec.Param(
          "date",
          "string",
          True,
          "The date to get the weather for in the format on YYYY-mm-dd.",
        ),
        FuncToolSpec.Param(
          "celsius",
          "bool",
          True,
          "Whether to use Celsius (True) or Fahrenheit (False) for the temperature.",
        ),
      ],
    )

  def _call(self, *, location, date, celsius, **kwargs) -> str:
    temperature = {
      # European cities
      "Zurich": 19,
      "London": 18,
      "Paris": 20,
      "Berlin": 21,
      "Madrid": 22,
      # American cities
      "New York": 25,
      "Los Angeles": 28,
      "Chicago": 24,
      "Miami": 30,
      "Houston": 29,
      "Toronto": 23,
      # Asian cities
      "Beijing": 26,
      "Shanghai": 27,
      "Chongqing": 29,
      "Hong Kong": 31,
      "Macau": 30,
      "Chinese Taipei": 28,
      "Tokyo": 22,
      "Seoul": 21,
      "Singapore": 30,
      "Mumbai": 32,
      # Australian cities
      "Sydney": 23,
      "Melbourne": 21,
      "Brisbane": 29,
      "Perth": 26,
    }.get(location, None)
    if temperature is None:
      raise FuncToolCallException("Error: Unknown location " + location)
    if celsius:
      temperature = f"{temperature}°C"
    else:
      temperature = f"{temperature * 9 / 5 + 32}°F"
    weather = ["Sunny", "Cloudy", "Rainy", "Windy", "Stormy"][random.randint(0, 10) % 5]
    air_quality = ["Good", "Fair", "Bad", "Extremely Bad"][random.randint(0, 10) % 4]
    wind_speed = random.randint(5, 20)  # km/h
    humidity = random.randint(30, 90)  # percentage
    return f"""\
Location: {location}
Date: {date}
Weather: {weather}
Temperature: {temperature}
Air Quality: {air_quality}
Wind Speed: {wind_speed} km/h
Humidity: {humidity}%"""


class GetAverage(FuncToolBase):
  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "get_average",
      "Calculate the average of a list of numbers.",
      [
        FuncToolSpec.Param(
          "numbers", "list[int|float]", True, "A list of numbers to sum up."
        )
      ],
    )

  def _call(self, *, numbers: list, **kwargs) -> str:
    if not isinstance(numbers, list):
      raise FuncToolCallException(
        f"The 'numbers' parameter must be a list, {type(numbers)} is given."
      )
    try:
      return str(sum(numbers) / len(numbers))
    except TypeError as e:
      raise FuncToolCallException(
        f"Elements of the 'numbers' list must be either integer or float: {e}"
      )


class FinishTask(FuncToolBase):
  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "finish",
      "When you have solved the user's issue, call this tool to let users know you're finished and present your result.",
      [
        FuncToolSpec.Param(
          "result", "string", True, "The final result presenting to the user."
        )
      ],
    )

  def _call(self, *, result: str, **kwargs) -> str:
    return result


def test_weather(agent_class, model: str):
  lm = agent_class(model=model, debug_mode=True)
  lm.console.print(f"Using model: {lm.model}")

  lm.register_tool(GetWeather(), 100)
  lm.register_tool(GetAverage(), 100)
  lm.register_tool(FinishTask(), 1)

  lm.append_user_message(
    "Please calculate the average temperature of all *European* cities shown below: New York, Beijing, Zurich, Chongqing, London, Berlin, Toronto, Shanghai, Seoul."
  )

  lm.run(
    ["get_average", "get_weather", "finish"],
    lambda x: (
      True,
      "Error: You're NOT calling any tool or you called with an INCORRECT format. Always select a tool to call with correct Tool Call Format. If you're done with the task, call the 'finish' tool with the result.",
    ),
    lambda tool, args, res: (
      tool != "finish",
      f"Good. The model gives the result: {res}" if tool == "finish" else res,
    ),
  )


if __name__ == "__main__":
  from autofix.lms.openai_generic import GPTGenericAgent

  test_weather(GPTGenericAgent, "gpt-5-mini")
