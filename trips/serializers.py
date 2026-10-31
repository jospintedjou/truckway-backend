from rest_framework import serializers


class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200)
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False)

    def validate(self, attrs):
        if ("lat" in attrs) != ("lon" in attrs):
            raise serializers.ValidationError("Send both lat and lon, or neither.")
        return attrs


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    departure_local = serializers.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        help_text="Local time at the origin, without offset (e.g. 2026-11-01T06:00).",
    )
    load_lb = serializers.FloatField(min_value=1, max_value=80_000)
    interval_mi = serializers.ChoiceField(choices=[10, 25, 50], default=25)
    scenario = serializers.ChoiceField(choices=["storm"], required=False, allow_null=True)
