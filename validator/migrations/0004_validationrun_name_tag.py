# Generated by Django 2.1 on 2018-10-06 15:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('validator', '0003_auto_20181005_1632'),
    ]

    operations = [
        migrations.AddField(
            model_name='validationrun',
            name='name_tag',
            field=models.CharField(blank=True, max_length=80),
        ),
    ]