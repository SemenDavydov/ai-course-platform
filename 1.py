# fix_materials.py
import os
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.material import Material
from app.models.course import Lesson


async def fix_materials():
    async with AsyncSessionLocal() as db:
        # Получаем все уроки
        lessons_result = await db.execute(select(Lesson))
        lessons = lessons_result.scalars().all()

        # Словарь с названиями материалов для каждого урока
        material_titles = {
            1: ["Подготовка к работе: чек-лист", "Инструменты для работы"],
            2: ["Сценарий: шаблон", "Пример сценария"],
            3: ["Раскадровка: примеры", "Шаблон раскадровки"],
            4: ["Анимация: техники", "Озвучка: гайд"],
            5: ["CapCut: горячие клавиши", "Эффекты в CapCut"],
            6: ["Ошибки новичков: памятка"],
            7: ["Промты: шпаргалка", "Примеры промтов"]
        }

        # Папка с файлами
        materials_dir = "uploads/materials"
        if not os.path.exists(materials_dir):
            print(f"❌ Папка {materials_dir} не найдена")
            return

        files = os.listdir(materials_dir)
        print(f"📁 Найдено файлов: {len(files)}")

        # Для каждого файла пытаемся определить, к какому уроку он относится
        file_index = 0
        for i, lesson in enumerate(lessons, 1):
            lesson_id = lesson.id
            titles = material_titles.get(i, [f"Материал к уроку {i}"])

            # Для каждого названия материала ищем файл
            for title_idx, title in enumerate(titles):
                if file_index >= len(files):
                    print(f"⚠️ Не хватает файлов для урока {i}")
                    break

                file_name = files[file_index]
                file_path = os.path.join(materials_dir, file_name)

                if not os.path.isfile(file_path):
                    file_index += 1
                    continue

                file_size = os.path.getsize(file_path)

                # Определяем тип файла
                if file_name.endswith('.pdf'):
                    file_type = 'application/pdf'
                    original_name = f"{title}.pdf"
                elif file_name.endswith('.odt'):
                    file_type = 'application/vnd.oasis.opendocument.text'
                    original_name = f"{title}.odt"
                else:
                    file_type = 'application/octet-stream'
                    original_name = file_name

                # Проверяем, есть ли уже такой материал
                existing = await db.execute(
                    select(Material).where(
                        Material.lesson_id == lesson_id,
                        Material.title == title
                    )
                )
                existing_material = existing.scalar_one_or_none()

                if not existing_material:
                    material = Material(
                        lesson_id=lesson_id,
                        title=title,
                        file_name=file_name,
                        original_name=original_name,
                        file_size=file_size,
                        file_type=file_type
                    )
                    db.add(material)
                    print(f"✅ Добавлен материал '{title}' для урока {i}")
                else:
                    print(f"⏩ Материал '{title}' уже существует для урока {i}")

                file_index += 1

        await db.commit()

        # Проверяем результат
        materials_result = await db.execute(select(Material))
        materials = materials_result.scalars().all()
        print(f"\n📊 Итого в базе: {len(materials)} материалов")

        for material in materials:
            print(f"   • Урок {material.lesson_id}: {material.title}")


if __name__ == "__main__":
    asyncio.run(fix_materials())