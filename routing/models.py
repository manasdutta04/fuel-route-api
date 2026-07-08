from django.db import models


class FuelStation(models.Model):
    """
    A truck-stop / fuel station from the OPIS price list, enriched with
    lat/lng coordinates so we can figure out which stations sit near a
    given route without ever calling a paid geocoder at request time.
    """

    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=4)

    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'{self.name} ({self.city}, {self.state}) - ${self.retail_price}'
