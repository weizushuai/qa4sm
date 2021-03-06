# Generated by Django 2.1.5 on 2019-03-11 07:41

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('validator', '0019_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='CeleryTask',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('celery_task', models.UUIDField()),
                ('validation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='validation', to='validator.ValidationRun')),
            ],
        ),
        migrations.AlterModelOptions(
            name='settings',
            options={'verbose_name_plural': 'Settings'},
        ),
    ]
